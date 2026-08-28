import html as html_lib
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha1
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests

from config import (
    EXA_API_KEY, FALLBACK_SOURCES, HEADERS, LOOKBACK_HOURS, MAX_EXA_ITEMS,
    MAX_SOURCE_ITEMS, PRIMARY_SOURCES, REQUEST_TIMEOUT,
)
from models import Candidate

log = logging.getLogger("gamingnewsroom.discovery")

session = requests.Session()

def now():
    return datetime.now(timezone.utc)

def iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    for parser in (
        lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
        lambda s: parsedate_to_datetime(s),
    ):
        try:
            dt = parser(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None

def canonical_url(url):
    try:
        p = urlparse(url)
        if not p.netloc:
            return ""
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid", "ref"}]
        return urlunparse(("https", p.netloc.lower().removeprefix("www."), re.sub(r"/+$", "", p.path or "/"), "", urlencode(query), ""))
    except Exception:
        return ""

def domain(url):
    return urlparse(url).netloc.lower().removeprefix("www.")

def allowed(url, domains: Iterable[str]):
    d = domain(url)
    return any(d == x or d.endswith("." + x) for x in domains)

def source_google_rss(domain_name):
    query = f"site:{domain_name} (gaming OR games OR videogame OR playstation OR xbox OR nintendo)"
    params = {
        "q": query + " when:3d",
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    }
    return "https://news.google.com/rss/search?" + urlencode(params)

def strip_tags(value):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_lib.unescape(value or ""))).strip()

def resolve_news_url(link):
    try:
        r = session.get(link, headers=HEADERS, timeout=8, allow_redirects=True, stream=True)
        final = canonical_url(r.url)
        r.close()
        if final and "news.google.com" not in final:
            return final
    except Exception:
        pass
    return canonical_url(link)

def xml_entries(data):
    root = ET.fromstring(data)
    out = []
    for item in root.iter():
        tag = item.tag.rsplit("}", 1)[-1]
        if tag not in {"item", "entry"}:
            continue
        fields = {}
        for child in list(item):
            key = child.tag.rsplit("}", 1)[-1]
            text = (child.text or "").strip()
            if key == "link" and not text:
                text = child.attrib.get("href", "")
            fields.setdefault(key, text)
            if key == "link" and child.attrib.get("href"):
                fields["link"] = child.attrib["href"]
        out.append(fields)
    return out

def fetch_feed(name, domain_name, tier, clock):
    url = source_google_rss(domain_name)
    try:
        response = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        entries = xml_entries(response.content)[:MAX_SOURCE_ITEMS]
    except Exception as exc:
        log.warning("SOURCE %-20s FAILED %s", name, exc)
        return [], {"ok": False, "entries": 0, "accepted": 0, "error": str(exc)[:250]}

    accepted = []
    rejected = 0
    for row in entries:
        title = strip_tags(row.get("title", ""))
        raw_link = row.get("link", "")
        url = resolve_news_url(raw_link) if "news.google.com" in raw_link else canonical_url(raw_link)
        pub = parse_dt(row.get("pubDate") or row.get("published") or row.get("updated"))
        if not title or not url or not pub or not allowed(url, [domain_name]):
            rejected += 1
            continue
        age = (clock - pub).total_seconds() / 3600
        if age < -1 or age > LOOKBACK_HOURS:
            rejected += 1
            continue
        accepted.append(Candidate(
            title=title,
            url=url,
            source=name,
            domain=domain_name,
            published_at=pub,
            summary=strip_tags(row.get("description", ""))[:1000],
            discovery="source-rss",
            tier=tier,
        ))
    log.info("SOURCE %-20s entries=%d accepted=%d rejected=%d", name, len(entries), len(accepted), rejected)
    return accepted, {"ok": True, "entries": len(entries), "accepted": len(accepted), "rejected": rejected}

def exa_search(query, domains, clock):
    if not EXA_API_KEY:
        return []
    body = {
        "query": query,
        "numResults": MAX_EXA_ITEMS,
        "includeDomains": domains,
        "startPublishedDate": iso(clock - timedelta(hours=LOOKBACK_HOURS)),
        "endPublishedDate": iso(clock + timedelta(minutes=5)),
        "contents": {"highlights": True},
        "type": "auto",
    }
    try:
        r = session.post(
            "https://api.exa.ai/search",
            headers={**HEADERS, "x-api-key": EXA_API_KEY, "Content-Type": "application/json"},
            json=body,
            timeout=35,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        log.warning("EXA FAILED: %s", exc)
        return []
    out = []
    for row in data.get("results", []):
        url = canonical_url(row.get("url", ""))
        pub = parse_dt(row.get("publishedDate") or row.get("published_date"))
        title = str(row.get("title") or "").strip()
        if not url or not pub or not title or not allowed(url, domains):
            continue
        if (clock - pub).total_seconds() / 3600 > LOOKBACK_HOURS:
            continue
        src_domain = domain(url)
        src_name = next((n for n, d, _ in PRIMARY_SOURCES if d == src_domain), src_domain)
        tier = next((t for n, d, t in PRIMARY_SOURCES if d == src_domain), 2)
        highlights = row.get("highlights") or []
        out.append(Candidate(title, url, src_name, src_domain, pub, " ".join(map(str, highlights))[:1000], row.get("image") or "", "exa", tier))
    log.info("EXA accepted=%d", len(out))
    return out

def dedupe(items):
    seen = set()
    out = []
    for item in sorted(items, key=lambda x: x.published_at, reverse=True):
        key = canonical_url(item.url)
        if key in seen or not key:
            continue
        seen.add(key)
        out.append(item)
    return out

def discover(clock=None, fallback=False):
    clock = clock or now()
    sources = FALLBACK_SOURCES if fallback else PRIMARY_SOURCES
    items = []
    health = {}
    for source in sources:
        name, domain_name, *tier = source
        t = tier[0] if tier else 2
        batch, stats = fetch_feed(name, domain_name, t, clock)
        items.extend(batch)
        health[name] = stats

    domains = [x[1] for x in sources]
    query = "important gaming news" if fallback else "major gaming news announcements releases outages acquisitions"
    items.extend(exa_search(query, domains, clock))
    return dedupe(items), health
