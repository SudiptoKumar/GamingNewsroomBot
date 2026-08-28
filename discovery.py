import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from config import EXA_API_KEY, FALLBACK_SOURCES, HEADERS, LOOKBACK_HOURS, MAX_EXA_ITEMS, MAX_SOURCE_ITEMS, PRIMARY_SOURCES, REQUEST_TIMEOUT
from models import Candidate

log = logging.getLogger("gamingnewsroom.discovery")
session = requests.Session()


def parse_dt(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    candidates = [text, text.replace("Z", "+00:00")]
    for candidate in candidates:
        try:
            dt = datetime.fromisoformat(candidate)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        dt = parsedate_to_datetime(text)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_url(url):
    try:
        p = urlparse(str(url or ""))
        if not p.netloc:
            return ""
        q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid", "ref"}]
        return urlunparse(("https", p.netloc.lower().removeprefix("www."), re.sub(r"/+$", "", p.path or "/"), "", urlencode(q), ""))
    except Exception:
        return ""


def domain(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


def allowed(url, domains):
    d = domain(url)
    return any(d == x or d.endswith("." + x) for x in domains)


def source_rss(domain_name):
    # Source-scoped Google News RSS is used as the stable transport layer. The final
    # article URL is resolved and validated back to the whitelisted publisher domain.
    query = f"site:{domain_name} (gaming OR game OR games OR playstation OR xbox OR nintendo OR steam) when:3d"
    return "https://news.google.com/rss/search?" + urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})


def strip_tags(value):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(str(value or "")))).strip()


def resolve_news_url(link):
    try:
        r = session.get(link, headers=HEADERS, timeout=8, allow_redirects=True, stream=True)
        final = canonical_url(r.url)
        r.close()
        return final or canonical_url(link)
    except Exception:
        return canonical_url(link)


def xml_entries(data):
    root = ET.fromstring(data)
    rows = []
    for item in root.iter():
        tag = item.tag.rsplit("}", 1)[-1]
        if tag not in {"item", "entry"}:
            continue
        row = {}
        for child in list(item):
            key = child.tag.rsplit("}", 1)[-1]
            text = (child.text or "").strip()
            if key == "link" and not text:
                text = child.attrib.get("href", "")
            row[key] = child.attrib.get("href", text) if key == "link" else text
        rows.append(row)
    return rows


def fetch_source(name, domain_name, tier, clock):
    stats = {"ok": False, "entries": 0, "accepted": 0, "rejected": 0, "unresolved": 0}
    try:
        r = session.get(source_rss(domain_name), headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        entries = xml_entries(r.content)[:MAX_SOURCE_ITEMS]
        stats["ok"] = True
        stats["entries"] = len(entries)
    except Exception as exc:
        stats["error"] = str(exc)[:240]
        log.warning("RSS source=%s FAILED=%s", name, exc)
        return [], stats

    out = []
    for row in entries:
        title = strip_tags(row.get("title"))
        raw_url = row.get("link", "")
        url = resolve_news_url(raw_url) if "news.google.com" in raw_url else canonical_url(raw_url)
        pub = parse_dt(row.get("pubDate") or row.get("published") or row.get("updated"))
        if not title or not url or not pub or not allowed(url, [domain_name]):
            stats["unresolved"] += 1
            continue
        age_hours = (clock - pub).total_seconds() / 3600
        if age_hours < -2 or age_hours > LOOKBACK_HOURS:
            stats["rejected"] += 1
            continue
        out.append(Candidate(title, url, name, domain_name, pub, strip_tags(row.get("description"))[:1200], "", "rss", tier))
    stats["accepted"] = len(out)
    log.info("RSS source=%s entries=%d accepted=%d rejected=%d unresolved=%d", name, stats["entries"], stats["accepted"], stats["rejected"], stats["unresolved"])
    return out, stats


def exa_search(query, sources, clock):
    if not EXA_API_KEY:
        return []
    domains = [d for _, d, _ in sources]
    body = {
        "query": query,
        "numResults": MAX_EXA_ITEMS,
        "includeDomains": domains,
        "startPublishedDate": iso(clock - timedelta(hours=LOOKBACK_HOURS)),
        "endPublishedDate": iso(clock + timedelta(minutes=5)),
        "contents": {"highlights": {"maxCharacters": 1200}},
        "type": "auto",
    }
    try:
        r = session.post("https://api.exa.ai/search", headers={"x-api-key": EXA_API_KEY, "Content-Type": "application/json", **HEADERS}, json=body, timeout=35)
        r.raise_for_status()
        payload = r.json()
    except Exception as exc:
        log.warning("EXA FAILED: %s", exc)
        return []
    out = []
    for row in payload.get("results", []):
        url = canonical_url(row.get("url"))
        title = strip_tags(row.get("title"))
        pub = parse_dt(row.get("publishedDate") or row.get("published_date"))
        if not url or not title or not pub or not allowed(url, domains):
            continue
        if (clock - pub).total_seconds() / 3600 > LOOKBACK_HOURS:
            continue
        src_domain = domain(url)
        src_name, tier = next(((n, t) for n, d, t in sources if d == src_domain), (src_domain, 2))
        highlights = row.get("highlights") or []
        out.append(Candidate(title, url, src_name, src_domain, pub, " ".join(map(str, highlights))[:1200], row.get("image") or "", "exa", tier))
    log.info("EXA accepted=%d", len(out))
    return out


def dedupe(items):
    seen = set()
    result = []
    for item in sorted(items, key=lambda x: x.published_at, reverse=True):
        u = canonical_url(item.url)
        if not u or u in seen:
            continue
        seen.add(u)
        result.append(item)
    return result


def discover(clock=None, fallback=False):
    clock = clock or datetime.now(timezone.utc)
    sources = FALLBACK_SOURCES if fallback else PRIMARY_SOURCES
    items, health = [], {}
    for name, dom, tier in sources:
        batch, stats = fetch_source(name, dom, tier, clock)
        items.extend(batch)
        health[name] = stats
    q = "important gaming news releases launches acquisitions outages security platform announcements" if not fallback else "important gaming news major announcements releases acquisitions outages"
    items.extend(exa_search(q, sources, clock))
    result = dedupe(items)
    log.info("DISCOVERY fallback=%s total=%d", fallback, len(result))
    return result, health
