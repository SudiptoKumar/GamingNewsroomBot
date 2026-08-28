import argparse
import logging
import time
from datetime import datetime, timezone

from config import CIRCUIT_BREAKER, LOOKBACK_HOURS, POST_DELAY, THIN_DAY_THRESHOLD, TELEGRAM_BOT_TOKEN, EXA_API_KEY, CEREBRAS_API_KEY
from discovery import discover, canonical_url, parse_dt
from editorial import cluster, generate, score
from publisher import publish
from state import load, load_posted, save

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("gamingnewsroom")


def fetch_article(url):
    import html, re, requests
    try:
        r = requests.get(url, timeout=22, headers={"User-Agent":"GamingNewsroom/1.0"})
        r.raise_for_status()
        source = r.text
        image = ""
        for pat in [r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image']:
            m = re.search(pat, source, re.I)
            if m:
                image = m.group(1); break
        body = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>", " ", source)
        body = re.sub(r"(?s)<[^>]+>", " ", body)
        body = re.sub(r"\s+", " ", html.unescape(body)).strip()
        return body[:9000], image
    except Exception as exc:
        log.warning("ARTICLE FETCH FAILED url=%s error=%s", url, exc)
        return "", ""


def validate_config():
    missing = [name for name, value in {"TELEGRAM_BOT_TOKEN":TELEGRAM_BOT_TOKEN,"EXA_API_KEY":EXA_API_KEY,"CEREBRAS_API_KEY":CEREBRAS_API_KEY}.items() if not value]
    if missing:
        raise RuntimeError("Missing required configuration: " + ", ".join(missing))


def run():
    validate_config()
    now = datetime.now(timezone.utc)
    state = load(); posted = load_posted()
    state["last_run"] = now.isoformat()
    candidates, health = discover(now, fallback=False)
    candidates = [x for x in candidates if canonical_url(x.url) not in posted]
    state["source_health"] = health
    log.info("PRIMARY CANDIDATES=%d", len(candidates))
    decisions = score(cluster(candidates))
    selected = sorted((d for d in decisions if d.important and d.score >= 7), key=lambda x:x.score, reverse=True)
    log.info("PRIMARY STORIES >=7=%d", len(selected))
    if len(selected) < THIN_DAY_THRESHOLD:
        fallback, _ = discover(now, fallback=True)
        fallback = [x for x in fallback if canonical_url(x.url) not in posted]
        fdec = score(cluster(fallback))
        keys = {d.event.key for d in selected}
        for d in sorted(fdec, key=lambda x:x.score, reverse=True):
            if d.important and d.score >= 7 and d.event.key not in keys:
                selected.append(d); keys.add(d.event.key)
        log.info("AFTER FALLBACK STORIES >=7=%d", len(selected))
    unique=[]; seen=set()
    for d in sorted(selected, key=lambda x:x.score, reverse=True):
        if d.event.key not in seen and d.event.key not in state.get("published_events", {}):
            seen.add(d.event.key); unique.append(d)
    selected = unique[:CIRCUIT_BREAKER]
    log.info("FINAL PUBLISHABLE STORIES=%d", len(selected))
    published_count=0
    for idx, decision in enumerate(selected,1):
        article, image = fetch_article(decision.event.representative.url)
        if image and not decision.event.representative.image_url:
            decision.event.representative.image_url=image
        story=generate(decision, article)
        if publish(story):
            key=decision.event.key
            state.setdefault("published_events",{})[key]={"title":story.headline,"url":decision.event.representative.url,"score":decision.score,"published_at":now.isoformat()}
            state.setdefault("queue",{})[decision.event.representative.url]={"status":"posted","title":story.headline}
            posted.add(canonical_url(decision.event.representative.url))
            published_count+=1
            save(state, posted)
            log.info("PUBLISHED #%d score=%.1f %s", idx, decision.score, story.headline)
        time.sleep(POST_DELAY)
    save(state, posted)
    log.info("DONE published=%d", published_count)
    return 0


def self_test():
    assert parse_dt("Fri, 28 Aug 2026 14:00:00 GMT").tzinfo is not None
    from publisher import self_test as rich_test
    assert rich_test()
    assert canonical_url("https://www.example.com/a?utm_source=x&x=1")=="https://example.com/a?x=1"
    log.info("GamingNewsroom V1 self-test passed")
    return 0

if __name__ == "__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--self-test", action="store_true")
    args=p.parse_args()
    raise SystemExit(self_test() if args.self_test else run())
