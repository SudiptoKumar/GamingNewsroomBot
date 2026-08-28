import json
import logging
import re
import time
from collections import Counter
from difflib import SequenceMatcher

import requests

from config import CEREBRAS_API_KEY, CEREBRAS_MODEL, THRESHOLD
from models import Candidate, Decision, Event, Story

log = logging.getLogger("gamingnewsroom.editorial")

SCORING_SYSTEM = """You are the senior editor for GamingNewsroom.
Score each event 0-10. Publish threshold is 7.0.
9-10: exceptional. Major launch/release, massive outage/security incident, industry-changing acquisition, or major move by Sony, Microsoft, Nintendo, Valve, Epic, Tencent, EA, Take-Two, Ubisoft, Activision Blizzard.
7-8: clearly important. Significant game update/expansion/launch, important platform/subscription/storefront/monetization change, significant security/anti-cheat change, major studio acquisition/closure, notable industry incident.
4-6: interesting but normally excluded.
0-3: routine patch notes, promotional fluff, reviews/opinion/listicles, weak rumors, off-topic tech.
Exclusivity and trending are supporting modifiers only. Leaks can publish, but confidence must be unconfirmed.
Return JSON only: {"classifications":[{"index":0,"score":8,"important":true,"exclusive":false,"confidence":"confirmed","trending":true,"category":"Major Release","reason":"..."}]}"""

GENERATION_SYSTEM = """You write factual, concise Telegram gaming news from supplied source material only.
Do not invent facts, dates, numbers, quotes, release windows, platforms, or claims.
Return JSON only:
{"headline":"...","summary":"...","highlights":["..."],"what_to_know":"...","platforms":["PS5","Xbox"],"hashtags":["#GamingNews","#PS5"],"badge":"Confirmed"}
Use 2-4 highlights. what_to_know should be a short paragraph. Platforms should be only platforms supported by the source text."""


def chat(system, user, attempts=4):
    if not CEREBRAS_API_KEY:
        raise RuntimeError("CEREBRAS_API_KEY missing")
    url = "https://api.cerebras.ai/v1/chat/completions"
    for attempt in range(attempts):
        try:
            r = requests.post(url, headers={"Authorization": f"Bearer {CEREBRAS_API_KEY}", "Content-Type": "application/json"}, json={"model": CEREBRAS_MODEL, "messages":[{"role":"system","content":system},{"role":"user","content":user}], "temperature":0.1, "max_tokens":2500}, timeout=75)
            if r.status_code == 429:
                retry = r.headers.get("Retry-After")
                wait = float(retry) if retry and retry.replace('.', '', 1).isdigit() else min(20, 2 ** attempt)
                log.warning("CEREBRAS 429; retrying in %.1fs", wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except requests.RequestException:
            if attempt == attempts - 1:
                raise
            time.sleep(min(20, 2 ** attempt))
    raise RuntimeError("Cerebras request failed")


def parse_json(text):
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*``\s*$", "", text.strip(), flags=re.I)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end+1])
        raise


def event_key(title):
    words = re.findall(r"[a-z0-9]{3,}", title.lower())
    stop = {"the","and","for","new","game","games","gaming","says","said","reveals","reveal","announced","announcement","latest","official","report","reports","major","news","update"}
    return " ".join(w for w in words if w not in stop)[:180]


def similar(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def cluster(candidates):
    events = []
    for candidate in candidates:
        placed = None
        key = event_key(candidate.title)
        for event in events:
            if key == event.key or similar(candidate.title, event.representative.title) >= 0.76:
                event.coverage.append(candidate)
                if candidate.tier < event.representative.tier or candidate.published_at > event.representative.published_at:
                    event.representative = candidate
                placed = event
                break
        if not placed:
            events.append(Event(key or candidate.title, candidate, [candidate]))
    return events


def score(events):
    if not events:
        return []
    payload = []
    for i, event in enumerate(events):
        payload.append({"index": i, "headline": event.representative.title, "source": event.representative.source, "sources": sorted({x.source for x in event.coverage}), "articles": [{"title":x.title,"source":x.source,"summary":x.summary[:500]} for x in event.coverage[:5]]})
    rows = {}
    try:
        parsed = parse_json(chat(SCORING_SYSTEM, json.dumps({"events": payload}, ensure_ascii=False)))
        rows = {int(x["index"]): x for x in parsed.get("classifications", [])}
    except Exception as exc:
        log.warning("SCORING FAILED: %s", exc)
    out = []
    for i, event in enumerate(events):
        row = rows.get(i, {})
        if row:
            s = max(0.0, min(10.0, float(row.get("score", 0))))
            important = bool(row.get("important", s >= THRESHOLD)) and s >= THRESHOLD
            confidence = row.get("confidence") if row.get("confidence") in {"confirmed","unconfirmed"} else "confirmed"
            out.append(Decision(event, s, important, bool(row.get("exclusive")), confidence, bool(row.get("trending", len(event.coverage) >= 3)), str(row.get("category","Industry / Business"))[:60], str(row.get("reason",""))[:300]))
        else:
            text = event.representative.title.lower()
            signals = ["launch","release","acquisition","acquire","layoff","shutdown","outage","hack","breach","security","state of play","nintendo direct","xbox showcase","price increase","expansion"]
            hits = sum(sig in text for sig in signals)
            s = min(8.0, 3.5 + hits * 1.2 + (1.0 if len(event.coverage) >= 3 else 0))
            out.append(Decision(event, s, s >= THRESHOLD, False, "confirmed", len(event.coverage) >= 3, "Industry / Business", "Fallback heuristic score."))
    return out


def generate(decision, article_text):
    rep = decision.event.representative
    material = article_text[:9000] or rep.summary[:2000] or rep.title
    user = json.dumps({"event":{"headline":rep.title,"score":decision.score,"category":decision.category,"confidence":decision.confidence,"sources":sorted({x.source for x in decision.event.coverage})},"article":{"source":rep.source,"url":rep.url,"title":rep.title,"text":material}}, ensure_ascii=False)
    try:
        data = parse_json(chat(GENERATION_SYSTEM, user))
    except Exception as exc:
        log.warning("GENERATION FAILED: %s", exc)
        data = {"headline":rep.title,"summary":rep.summary[:240] or rep.title,"highlights":[],"what_to_know":"","platforms":[],"hashtags":["#GamingNews"],"badge":"Unconfirmed" if decision.confidence == "unconfirmed" else "Confirmed"}
    return Story(decision, str(data.get("headline") or rep.title)[:180], str(data.get("summary") or rep.summary[:240])[:320], [str(x) for x in (data.get("highlights") or [])][:4], str(data.get("what_to_know") or ""), [str(x) for x in (data.get("platforms") or [])][:6], [str(x) for x in (data.get("hashtags") or ["#GamingNews"])][:10], str(data.get("badge") or ("Unconfirmed" if decision.confidence == "unconfirmed" else "Confirmed")), rep.image_url)
