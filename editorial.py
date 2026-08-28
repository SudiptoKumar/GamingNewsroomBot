import json
import logging
import re
from collections import defaultdict
from difflib import SequenceMatcher

import requests

from config import CEREBRAS_API_KEY, CEREBRAS_MODEL, THRESHOLD
from models import Candidate, Decision, Event, Story

log = logging.getLogger("gamingnewsroom.editorial")

PLATFORMS = ["PC", "PS5", "PS4", "Xbox", "Switch", "Mobile"]
LOW_VALUE = re.compile(r"\b(review|unboxing|first look|best games|deals?|listicle|podcast|opinion)\b", re.I)

SCORING_SYSTEM = """You are the GamingNewsroom editor.
Score each event from 0 to 10 using this rubric:
9-10 = exceptional: major launch/release, massive outage/security incident, industry-changing acquisition, major move by Sony/Microsoft/Nintendo/Valve/Epic/Tencent/EA/Take-Two/Ubisoft/Activision Blizzard.
7-8 = clearly important: significant game update/expansion/launch, important platform/subscription/monetization change, major security/anti-cheat change, notable acquisition/closure/industry incident.
4-6 = interesting but normally excluded.
0-3 = low importance: promotional fluff, routine patches, reviews/opinion/listicles, weak rumors.
Exclusivity and trending are modifiers, never substitutes for importance.
Leaks may publish only as unconfirmed.
Return valid JSON only with one classification per index.
Schema: {"classifications":[{"index":0,"score":8,"important":true,"exclusive":false,"confidence":"confirmed","trending":true,"category":"Major Release","reason":"..."}]}"""

GENERATION_SYSTEM = """You write concise factual Telegram gaming news.
Use only the supplied article material. Never invent facts, numbers, dates, quotes, platforms or release windows.
Return valid JSON only:
{"headline":"...","summary":"...","highlights":["..."],"what_to_know":[{"term":"...","meaning":"..."}],"platforms":["PS5","PC"],"hashtags":["#GamingNews","#PS5"],"badge":"Confirmed"}"""

def chat(system, user):
    if not CEREBRAS_API_KEY:
        raise RuntimeError("CEREBRAS_API_KEY missing")
    r = requests.post(
        "https://api.cerebras.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {CEREBRAS_API_KEY}", "Content-Type": "application/json"},
        json={"model": CEREBRAS_MODEL, "messages":[{"role":"system","content":system},{"role":"user","content":user}], "temperature":0.1, "max_tokens":6000},
        timeout=75,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def parse_json(text):
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip(), flags=re.I)
    try:
        return json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end+1])
        raise

def key(title):
    words = re.findall(r"[a-z0-9]{3,}", title.lower())
    stop = {"the","and","for","new","game","games","gaming","says","reveals","announces","latest","official","report","reports","major","news"}
    kept = [w for w in words if w not in stop]
    return " ".join(kept[:16])

def similar(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def cluster(items):
    events = []
    buckets = {}
    for item in items:
        placed = None
        base = key(item.title)
        for event in events:
            rep = event.representative
            if base == key(rep.title) or similar(item.title, rep.title) >= 0.73:
                placed = event
                break
        if placed:
            placed.coverage.append(item)
        else:
            e = Event(base or item.title, item, [item])
            events.append(e)
    return events

def score(events):
    if not events:
        return []
    payload = []
    for i, e in enumerate(events):
        payload.append({
            "index": i,
            "headline": e.representative.title,
            "source": e.representative.source,
            "sources": sorted({x.source for x in e.coverage}),
            "articles": [{"title": x.title, "source": x.source, "summary": x.summary[:400]} for x in e.coverage[:5]],
        })
    try:
        result = parse_json(chat(SCORING_SYSTEM, json.dumps({"events": payload}, ensure_ascii=False)))
        rows = {int(x["index"]): x for x in result.get("classifications", [])}
    except Exception as exc:
        log.warning("SCORING FAILED, using conservative heuristic: %s", exc)
        rows = {}
    decisions = []
    for i, e in enumerate(events):
        row = rows.get(i, {})
        if row:
            s = max(0, min(10, float(row.get("score", 0))))
            reason = str(row.get("reason", ""))[:280]
            confidence = row.get("confidence", "confirmed")
            category = row.get("category", "Industry / Business")
            exclusive = bool(row.get("exclusive", False))
            trending = bool(row.get("trending", len(e.coverage) >= 3))
        else:
            text = e.representative.title.lower()
            hits = sum(x in text for x in ("announces", "announced", "launch", "release", "acquisition", "acquire", "layoff", "shutdown", "outage", "hack", "breach", "security", "state of play", "nintendo direct", "xbox showcase", "price increase"))
            s = min(8, 4 + hits * 1.1)
            confidence, category, exclusive, trending = "confirmed", "Industry / Business", False, len(e.coverage) >= 3
            reason = "Fallback score from high-signal gaming event terms."
        decisions.append(Decision(e, s, s >= THRESHOLD, exclusive, confidence, trending, category, reason))
    return decisions

def infer_platforms(text):
    low = text.lower()
    return [p for p in PLATFORMS if p.lower() in low][:5]

def generate(decision, article_text, image_url=""):
    material = article_text[:7000] or decision.event.representative.summary or decision.event.representative.title
    user = json.dumps({
        "event": {
            "headline": decision.event.representative.title,
            "score": decision.score,
            "category": decision.category,
            "confidence": decision.confidence,
            "exclusive": decision.exclusive,
            "trending": decision.trending,
            "sources": sorted({x.source for x in decision.event.coverage}),
            "reason": decision.reason,
        },
        "article": {
            "source": decision.event.representative.source,
            "url": decision.event.representative.url,
            "title": decision.event.representative.title,
            "text": material,
        },
    }, ensure_ascii=False)
    try:
        data = parse_json(chat(GENERATION_SYSTEM, user))
    except Exception as exc:
        log.warning("GENERATION FAILED for %s: %s", decision.event.representative.title, exc)
        data = {
            "headline": decision.event.representative.title,
            "summary": decision.event.representative.summary[:220] or decision.event.representative.title,
            "highlights": [],
            "what_to_know": [],
            "platforms": infer_platforms(decision.event.representative.title + " " + material),
            "hashtags": ["#GamingNews"],
            "badge": "Unconfirmed" if decision.confidence != "confirmed" else "Confirmed",
        }
    return Story(decision, data.get("headline") or decision.event.representative.title,
                 data.get("summary") or "", data.get("highlights") or [],
                 data.get("what_to_know") or [], data.get("platforms") or [],
                 data.get("hashtags") or ["#GamingNews"], data.get("badge") or "Confirmed",
                 material, image_url)
