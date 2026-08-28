import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from config import CIRCUIT_BREAKER, THIN_DAY_THRESHOLD, POST_DELAY
from discovery import discover, dedupe
from editorial import cluster, score, generate
from publisher import fetch_article, publish
import state

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("gamingnewsroom")

def run():
    now = datetime.now(timezone.utc)
    st = state.load()
    posted = state.load_posted()
    st["last_run"] = now.isoformat()

    primary, health = discover(now, fallback=False)
    primary = [c for c in primary if c.url not in posted]
    st["source_health"] = health
    log.info("PRIMARY CANDIDATES=%d", len(primary))

    events = cluster(primary)
    log.info("EVENT CLUSTERS=%d", len(events))
    decisions = score(events)
    publishable = [d for d in decisions if d.important and d.score >= 7]
    log.info("PRIMARY STORIES >=7=%d", len(publishable))

    # Fallback is a second source set, activated only when the primary pool is thin.
    if len(publishable) < THIN_DAY_THRESHOLD:
        fallback, _ = discover(now, fallback=True)
        fallback = [c for c in fallback if c.url not in posted]
        if fallback:
            fallback_decisions = score(cluster(fallback))
            keys = {d.event.key for d in publishable}
            for d in fallback_decisions:
                if d.important and d.score >= 7 and d.event.key not in keys:
                    publishable.append(d)
                    keys.add(d.event.key)
        log.info("AFTER FALLBACK STORIES >=7=%d", len(publishable))

    # Event-level dedup and engineering circuit breaker.
    unique = {}
    for d in sorted(publishable, key=lambda x: x.score, reverse=True):
        if d.event.key not in st["published_events"]:
            unique[d.event.key] = d
    publishable = list(unique.values())[:CIRCUIT_BREAKER]
    log.info("FINAL PUBLISHABLE STORIES=%d", len(publishable))

    for index, decision in enumerate(publishable, 1):
        article_text, image_url = fetch_article(decision.event.representative.url)
        story = generate(decision, article_text, image_url)
        # Keep the source URL as the publication identity. A successful Telegram call
        # is the only point at which this event enters publication history.
        if publish(story):
            key = decision.event.key
            posted.add(decision.event.representative.url)
            st["published_events"][key] = {
                "title": story.headline,
                "url": decision.event.representative.url,
                "score": decision.score,
                "published_at": now.isoformat(),
            }
            st["queue"][decision.event.representative.url] = {"status": "posted", "title": story.headline}
            state.save(st, posted)
            log.info("PUBLISHED #%d score=%.1f %s", index, decision.score, story.headline)
        time.sleep(POST_DELAY)

    state.save(st, posted)
    log.info("DONE published=%d", sum(1 for d in publishable if d.event.key in st["published_events"]))
    return 0

def self_test():
    from discovery import canonical_url, parse_dt, xml_entries
    from editorial import cluster
    from models import Candidate
    from publisher import build_rich_html
    import json
    now = datetime.now(timezone.utc)
    assert parse_dt("2026-08-28T13:00:00Z").tzinfo is not None
    payload = {"startPublishedDate": now.isoformat(), "endPublishedDate": now.isoformat()}
    json.dumps(payload)
    xml = b"""<rss version="2.0"><channel><item><title>Fresh gaming story</title><link>https://ign.com/articles/test</link><pubDate>Fri, 28 Aug 2026 14:00:00 GMT</pubDate></item></channel></rss>"""
    entries = xml_entries(xml)
    assert entries and entries[0]["title"] == "Fresh gaming story"
    assert parse_dt(entries[0]["pubDate"]).tzinfo is not None
    a = Candidate("Major PlayStation game announced", "https://ign.com/articles/a", "IGN", "ign.com", now)
    b = Candidate("Major PlayStation game announced today", "https://gamespot.com/articles/b", "GameSpot", "gamespot.com", now)
    assert len(cluster([a, b])) == 1
    assert canonical_url("https://www.example.com/a?utm_source=x&x=1") == "https://example.com/a?x=1"
    cap = build_rich_html(__import__("models").Story(
        __import__("models").Decision(__import__("models").Event("x", a, [a]), 8, True, False, "confirmed", True, "Major Release", "test"),
        "A major game announced", "The studio announced a new game.", ["Announcement is official."],
        [{"term":"Platform","meaning":"PS5 and PC."}], ["PS5","PC"], ["#GamingNews","#PS5"], "Confirmed", "", ""
    ))
    assert len(cap) <= 1024
    assert "<pre>" in cap and "<details>" in cap and '<a href="https://ign.com/articles/a">IGN</a>' in cap
    log.info("GamingNewsroom fresh self-test passed")
    return 0

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args()
    sys.exit(self_test() if args.self_test else run())
