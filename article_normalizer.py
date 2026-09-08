from __future__ import annotations
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import re

TRACKING = {"utm_source","utm_medium","utm_campaign","utm_term","utm_content","gclid","fbclid","ref"}

def canonical_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw: return ""
    parts = urlsplit(raw)
    query = [(k,v) for k,v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING]
    path = re.sub(r"/+", "/", parts.path or "/").rstrip("/") or "/"
    host = parts.netloc.lower().removeprefix("www.")
    return (host + path if not query else host + path + "?" + urlencode(query))

def normalize_article(item: dict) -> dict:
    out = dict(item)
    out["canonical"] = canonical_url(out.get("url") or out.get("canonical") or "")
    out["title"] = re.sub(r"\s+", " ", str(out.get("title") or "")).strip()
    out["excerpt"] = re.sub(r"\s+", " ", str(out.get("excerpt") or "")).strip()
    return out

def hard_dedup(items: list[dict]) -> list[dict]:
    seen_url, seen_title = set(), set()
    result = []
    for item in items:
        x = normalize_article(item)
        if x["canonical"] and x["canonical"] in seen_url: continue
        title_key = re.sub(r"[^a-z0-9]+", " ", x["title"].lower()).strip()
        source_key = (str(x.get("source") or "").strip().lower(), title_key)
        if title_key and source_key in seen_title: continue
        if x["canonical"]: seen_url.add(x["canonical"])
        if title_key: seen_title.add(source_key)
        result.append(x)
    return result
