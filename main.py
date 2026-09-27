#!/usr/bin/env python3
"""Gaming News

Simple, RSS-first Telegram gaming news publisher.

Design:
- Trusted RSS is the publication gate.
- Exa is used only when RSS coverage is insufficient.
- Cerebras is used once per run to format up to six posts in one batch.
- If Cerebras or article extraction fails, the bot falls back to deterministic source text.
- Trusted RSS controls article selection; AI is used only for formatting.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit

import feedparser
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
import trafilatura
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from cerebras.cloud.sdk import Cerebras
except Exception as exc:  # pragma: no cover - handled by runtime checks
    Cerebras = None
    CEREBRAS_IMPORT_ERROR = exc
else:
    CEREBRAS_IMPORT_ERROR = None


APP_NAME = "Gaming News"
CHANNEL_USERNAME = "@GamingNewsroom"
DEFAULT_CHANNEL = "@GamingNewsroom"
DEFAULT_MODEL = "gpt-oss-120b"
PUBLISH_TARGET = 6
WINDOW_HOURS = 24
EXA_MAX_RESULTS = 20
MAX_ARTICLE_CHARS_FOR_AI = 2200
MAX_EXA_QUERIES_PER_RUN = 1
CANDIDATE_POOL_SIZE = 12
MAX_CEREBRAS_CALLS_PER_RUN = 1

ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "news_state.json"
POSTED_PATH = ROOT / "posted_urls.txt"
TELEGRAM_CHANNEL = os.getenv("TELEGRAM_CHANNEL", DEFAULT_CHANNEL).strip() or DEFAULT_CHANNEL
EXA_API_KEY = os.getenv("EXA_API_KEY", "").strip()
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CEREBRAS_MODEL = os.getenv("CEREBRAS_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL

HEADERS = {
    "User-Agent": "GamingNews/1.0 (+https://github.com/)",
    "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
}

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "ref", "ref_src", "mc_cid", "mc_eid",
}

TRUSTED_SOURCES = [
    {"name": "IGN", "domain": "ign.com", "rss": "https://feeds.ign.com/ign/all"},
    {"name": "GameSpot", "domain": "gamespot.com", "rss": "https://www.gamespot.com/feeds/mashup/"},
    {"name": "VGC", "domain": "videogameschronicle.com", "rss": "https://www.videogameschronicle.com/feed/"},
    {"name": "Eurogamer", "domain": "eurogamer.net", "rss": "https://www.eurogamer.net/?format=rss"},
    {"name": "Gematsu", "domain": "gematsu.com", "rss": "https://www.gematsu.com/feed"},
    {"name": "PC Gamer", "domain": "pcgamer.com", "rss": "https://www.pcgamer.com/feeds.xml"},
    {"name": "Polygon", "domain": "polygon.com", "rss": "https://www.polygon.com/rss/index.xml"},
    {"name": "Kotaku", "domain": "kotaku.com", "rss": "https://kotaku.com/rss"},
    {"name": "GamesRadar+", "domain": "gamesradar.com", "rss": "https://www.gamesradar.com/feeds.xml"},
    {"name": "Rock Paper Shotgun", "domain": "rockpapershotgun.com", "rss": "http://feeds.feedburner.com/RockPaperShotgun"},
    {"name": "Game Developer", "domain": "gamedeveloper.com", "rss": "https://www.gamedeveloper.com/rss.xml"},
    {"name": "Insider Gaming", "domain": "insider-gaming.com", "rss": "https://insider-gaming.com/feed/"},
    {"name": "Nintendo Life", "domain": "nintendolife.com", "rss": "https://www.nintendolife.com/feeds/latest"},
    {"name": "Push Square", "domain": "pushsquare.com", "rss": "https://www.pushsquare.com/feeds/latest"},
    {"name": "Pure Xbox", "domain": "purexbox.com", "rss": "https://www.purexbox.com/feeds/latest"},
    {"name": "Shacknews", "domain": "shacknews.com", "rss": "https://www.shacknews.com/feed"},
    {"name": "Siliconera", "domain": "siliconera.com", "rss": "https://www.siliconera.com/feed/"},
    {"name": "VG247", "domain": "vg247.com", "rss": "https://www.vg247.com/feed"},
    {"name": "TechRaptor", "domain": "techraptor.net", "rss": "https://techraptor.net/gaming/rss.xml"},
    {"name": "The Escapist", "domain": "escapistmagazine.com", "rss": "https://www.escapistmagazine.com/v2/feed/"},
]
TRUSTED_DOMAINS = [x["domain"] for x in TRUSTED_SOURCES]
SOURCE_BY_DOMAIN = {x["domain"]: x["name"] for x in TRUSTED_SOURCES}
LOGO_URL_CACHE: dict[str, str] = {}

PLATFORMS = ("PlayStation", "Xbox", "PC Game", "Mobile Game")
PLATFORM_HASHTAGS = {
    "PlayStation": "#PlayStation",
    "Xbox": "#Xbox",
    "PC Game": "#PCGaming",
    "Mobile Game": "#MobileGaming",
}

logger = logging.getLogger(APP_NAME)


def setup_logging() -> None:
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    logger.addHandler(stream)


setup_logging()


retry_policy = Retry(
    total=1,
    connect=1,
    read=1,
    status=1,
    backoff_factor=0.2,
    status_forcelist=(429, 500, 502, 503, 504),
    allowed_methods=frozenset({"GET", "HEAD"}),
    respect_retry_after_header=False,
)
session = requests.Session()
adapter = HTTPAdapter(max_retries=retry_policy, pool_connections=20, pool_maxsize=20)
session.mount("http://", adapter)
session.mount("https://", adapter)


def safe_text(value: object) -> str:
    return "" if value is None else str(value).strip()


def canonical_url(url: str) -> str:
    raw = safe_text(url)
    if not raw:
        return ""
    p = urlsplit(raw)
    host = p.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+/", "/", p.path or "/")
    if path != "/":
        path = path.rstrip("/")
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS]
    return f"{host}{path}" + (f"?{urlencode(query)}" if query else "")


def normalize_title(title: str) -> str:
    text = safe_text(title).lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similar_title(a: str, b: str) -> bool:
    # Keep deduplication intentionally simple: exact normalized titles only.
    # Trusted sources can publish distinct stories with similar wording, so a
    # fuzzy threshold is too aggressive for this simple RSS-first design.
    na, nb = normalize_title(a), normalize_title(b)
    return bool(na and nb and na == nb)


def parse_date(value: object) -> datetime | None:
    raw = safe_text(value)
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        try:
            dt = parsedate_to_datetime(raw)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"version": APP_NAME, "feeds": {}, "last_run": None, "stats": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state is not an object")
        data.setdefault("version", APP_NAME)
        data.setdefault("feeds", {})
        data.setdefault("last_run", None)
        data.setdefault("stats", {})
        return data
    except Exception as exc:
        logger.warning("State reset because it could not be read: %s", exc)
        return {"version": APP_NAME, "feeds": {}, "last_run": None, "stats": {}}


def save_state(state: dict) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def load_posted_urls() -> set[str]:
    if not POSTED_PATH.exists():
        return set()
    values = set()
    for line in POSTED_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        key = canonical_url(line)
        if key:
            values.add(key)
    return values


def append_posted_url(url: str) -> None:
    key = canonical_url(url)
    if not key:
        return
    with POSTED_PATH.open("a", encoding="utf-8") as f:
        f.write(key + "\n")


def source_name_from_url(url: str) -> str:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    for domain, name in SOURCE_BY_DOMAIN.items():
        if host == domain or host.endswith("." + domain):
            return name
    return host or "Source"


def source_homepage(domain: str) -> str:
    return f"https://{domain}/"


def entry_image(entry: dict) -> str:
    for key in ("media_content", "media_thumbnail"):
        items = entry.get(key) or []
        for item in items:
            url = safe_text(item.get("url")) if isinstance(item, dict) else ""
            if url:
                return url
    for enc in entry.get("enclosures") or []:
        if isinstance(enc, dict) and "image" in safe_text(enc.get("type")):
            url = safe_text(enc.get("href") or enc.get("url"))
            if url:
                return url
    return ""


def parse_feed_entries(feed_def: dict, raw_feed: bytes, cutoff: datetime) -> list[dict]:
    parsed = feedparser.parse(raw_feed)
    items = []
    for entry in parsed.entries:
        url = safe_text(entry.get("link"))
        title = safe_text(entry.get("title"))
        if not url or not title:
            continue
        published = None
        for key in ("published", "updated", "created", "pubDate"):
            published = parse_date(entry.get(key))
            if published:
                break
        if not published:
            if entry.get("published_parsed"):
                try:
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                except Exception:
                    pass
        if not published or published < cutoff:
            continue
        excerpt = safe_text(entry.get("summary") or entry.get("description"))
        soup = BeautifulSoup(excerpt, "html.parser")
        excerpt = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
        image = entry_image(entry)
        items.append({
            "title": re.sub(r"\s+", " ", title).strip(),
            "url": url,
            "canonical": canonical_url(url),
            "published_at": published.isoformat(),
            "source": feed_def["name"],
            "domain": feed_def["domain"],
            "excerpt": excerpt[:1600],
            "image_url": image,
            "discovery": "rss",
        })
    return items


def collect_rss(state: dict, cutoff: datetime) -> list[dict]:
    all_items: list[dict] = []
    state.setdefault("feeds", {})
    logger.info("RSS: checking %d trusted feeds | window=%sh", len(TRUSTED_SOURCES), WINDOW_HOURS)
    for feed in TRUSTED_SOURCES:
        meta = state["feeds"].setdefault(feed["rss"], {})
        headers = dict(HEADERS)
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]
        try:
            response = session.get(feed["rss"], headers=headers, timeout=12)
            meta["last_checked"] = iso_now()
            meta["status"] = response.status_code
            if response.status_code == 304:
                logger.info("RSS 304: %s", feed["name"])
                continue
            if response.status_code >= 400:
                meta["fail_count"] = int(meta.get("fail_count", 0)) + 1
                logger.warning("RSS %s returned HTTP %s", feed["name"], response.status_code)
                continue
            meta["etag"] = response.headers.get("ETag")
            meta["last_modified"] = response.headers.get("Last-Modified")
            meta["fail_count"] = 0
            items = parse_feed_entries(feed, response.content, cutoff)
            logger.info("RSS %s: %d recent articles", feed["name"], len(items))
            all_items.extend(items)
        except Exception as exc:
            meta["last_checked"] = iso_now()
            meta["fail_count"] = int(meta.get("fail_count", 0)) + 1
            logger.warning("RSS %s failed: %s", feed["name"], exc)
    return dedup_items(all_items)


def dedup_items(items: list[dict]) -> list[dict]:
    seen_urls: set[str] = set()
    seen_titles: list[str] = []
    output: list[dict] = []
    for item in sorted(items, key=lambda x: parse_date(x.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        key = canonical_url(item.get("url", ""))
        if not key or key in seen_urls:
            continue
        if any(similar_title(item.get("title", ""), old) for old in seen_titles[-80:]):
            continue
        item = dict(item)
        item["canonical"] = key
        seen_urls.add(key)
        seen_titles.append(item.get("title", ""))
        output.append(item)
    return output


def exa_search_fallback(cutoff: datetime, needed: int) -> list[dict]:
    if not EXA_API_KEY or needed <= 0:
        return []
    now = datetime.now(timezone.utc)
    payload = {
        "query": "latest gaming news PlayStation Xbox Nintendo PC Steam game industry",
        "includeDomains": TRUSTED_DOMAINS,
        "startPublishedDate": cutoff.isoformat().replace("+00:00", "Z"),
        "endPublishedDate": now.isoformat().replace("+00:00", "Z"),
        "numResults": min(EXA_MAX_RESULTS, max(10, needed * 3)),
        "contents": {"highlights": {"maxCharacters": 900}},
    }
    try:
        response = session.post(
            "https://api.exa.ai/search",
            json=payload,
            headers={"x-api-key": EXA_API_KEY, "Content-Type": "application/json"},
            timeout=12,
        )
        if response.status_code >= 400:
            logger.warning("Exa fallback HTTP %s; continuing without Exa", response.status_code)
            return []
        data = response.json()
        results = data.get("results") or []
        output = []
        for result in results:
            url = safe_text(result.get("url"))
            title = safe_text(result.get("title"))
            if not url or not title or not any((urlparse(url).netloc or "").lower().endswith(d) for d in TRUSTED_DOMAINS):
                continue
            source = source_name_from_url(url)
            highlights = result.get("highlights") or []
            excerpt = safe_text(highlights[0]) if highlights else safe_text(result.get("summary") or result.get("text"))
            published = parse_date(result.get("publishedDate")) or now
            output.append({
                "title": title,
                "url": url,
                "canonical": canonical_url(url),
                "published_at": published.isoformat(),
                "source": source,
                "domain": urlsplit(url).netloc.lower().removeprefix("www."),
                "excerpt": excerpt[:1600],
                "image_url": safe_text(result.get("image")),
                "discovery": "exa",
            })
        logger.info("Exa fallback: %d results", len(output))
        return dedup_items(output)
    except Exception as exc:
        logger.warning("Exa fallback failed: %s", exc)
        return []


def find_og_image(url: str, page_html: str | None = None, final_url: str | None = None) -> str:
    try:
        base = final_url or url
        if page_html is None:
            response = session.get(url, headers=HEADERS, timeout=15)
            if response.status_code >= 400:
                return ""
            page_html = response.text
            base = response.url
        soup = BeautifulSoup(page_html, "html.parser")
        for attrs in ({"property": "og:image"}, {"property": "og:image:url"}, {"name": "twitter:image"}):
            tag = soup.find("meta", attrs=attrs)
            if tag and safe_text(tag.get("content")):
                return urljoin(base, safe_text(tag["content"]))
    except Exception:
        pass
    return ""


def extract_article(item: dict) -> tuple[str, str]:
    url = item["url"]
    image_url = safe_text(item.get("image_url"))
    rss_excerpt = safe_text(item.get("excerpt"))
    # Trust the RSS excerpt when it is already substantial; only fetch the
    # article page when more content or an image is needed.
    if len(rss_excerpt) >= 900 and image_url:
        return rss_excerpt, image_url
    try:
        response = session.get(url, headers={**HEADERS, "Referer": url}, timeout=12)
        if response.status_code < 400:
            page_html = response.text
            image_url = image_url or find_og_image(url, page_html, response.url)
            text = trafilatura.extract(page_html, include_comments=False, include_tables=False, favor_precision=True)
            if text and len(text.strip()) >= 450:
                return text.strip(), image_url
    except Exception as exc:
        logger.info("ARTICLE fetch fallback: %s | %s", item["source"], exc)
    return rss_excerpt, image_url


def clean_generated_text(text: object) -> str:
    value = safe_text(text)
    value = re.sub(r"\*\*(.*?)\*\*", r"\1", value, flags=re.S)
    value = re.sub(r"__(.*?)__", r"\1", value, flags=re.S)
    value = value.replace("`", "")
    value = re.sub(r"^#+\s*", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def first_sentence(text: str) -> str:
    cleaned = clean_generated_text(text)
    m = re.search(r"(.+?[.!?])(?:\s|$)", cleaned)
    return (m.group(1) if m else cleaned).strip()


def infer_platform(item: dict, article_text: str) -> str:
    text = (safe_text(item.get("title")) + " " + safe_text(article_text)).lower()
    if any(x in text for x in ("playstation", "ps5", "ps4")):
        return "PlayStation"
    if any(x in text for x in ("xbox", "game pass")):
        return "Xbox"
    if any(x in text for x in ("android", "ios", "iphone", "mobile game")):
        return "Mobile Game"
    return "PC Game"


def default_hashtags(story: dict) -> list[str]:
    platform = story.get("platform") if story.get("platform") in PLATFORMS else "PC Game"
    return ["#Gaming", PLATFORM_HASHTAGS[platform], "#GameNews"]


STORY_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "platform": {"type": "string", "enum": list(PLATFORMS)},
        "highlights": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 5},
        "hashtags": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3},
    },
    "required": ["id", "headline", "summary", "platform", "highlights", "hashtags"],
    "additionalProperties": False,
}
STORY_SCHEMA = {
    "type": "object",
    "properties": {"posts": {"type": "array", "items": STORY_ITEM_SCHEMA}},
    "required": ["posts"],
    "additionalProperties": False,
}


def valid_story(story: dict) -> tuple[bool, list[str]]:
    errors = []
    headline = clean_generated_text(story.get("headline"))
    summary = first_sentence(story.get("summary"))
    platform = story.get("platform")
    highlights = [clean_generated_text(x) for x in story.get("highlights", []) if clean_generated_text(x)]
    hashtags = [safe_text(x) for x in story.get("hashtags", []) if safe_text(x)]
    if not headline:
        errors.append("headline")
    if not summary:
        errors.append("summary")
    if platform not in PLATFORMS:
        errors.append("platform")
    if not 3 <= len(highlights) <= 5:
        errors.append("highlights")
    if not 1 <= len(hashtags) <= 3:
        errors.append("hashtags")
    combined = " ".join([headline, summary, *highlights])
    if "**" in combined or "__" in combined or "`" in combined:
        errors.append("markdown")
    if any(not x.endswith((".", "!", "?")) for x in [summary, *highlights]):
        errors.append("sentence")
    return not errors, errors


def sanitize_ai_story(raw: dict, item: dict) -> dict | None:
    story = {
        **item,
        "headline": clean_generated_text(raw.get("headline")),
        "summary": first_sentence(raw.get("summary")),
        "platform": safe_text(raw.get("platform")),
        "highlights": [clean_generated_text(x) for x in (raw.get("highlights") or [])],
        "hashtags": [],
    }
    hashtags = []
    for tag in raw.get("hashtags") or []:
        tag = safe_text(tag)
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = "#" + re.sub(r"[^A-Za-z0-9_]", "", tag)
        if tag not in hashtags:
            hashtags.append(tag)
    story["hashtags"] = hashtags[:3]
    if len(story["hashtags"]) < 2:
        story["hashtags"] = default_hashtags(story)
    story["highlights"] = story["highlights"][:5]
    ok, errors = valid_story(story)
    if not ok:
        logger.warning("Story rejected after Cerebras formatting: %s", errors)
        return None
    return story


def build_deterministic_story(item: dict, article_text: str) -> dict | None:
    source_text = re.sub(r"\s+", " ", safe_text(article_text)).strip()
    if not source_text:
        source_text = safe_text(item.get("excerpt"))
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", source_text) if len(s.strip()) >= 35]
    if not sentences:
        sentences = [safe_text(item.get("excerpt"))] if safe_text(item.get("excerpt")) else []
    if not sentences:
        return None
    highlights: list[str] = []
    seen = set()
    for sentence in sentences:
        sentence = first_sentence(sentence)
        key = normalize_title(sentence)
        if key and key not in seen:
            seen.add(key)
            highlights.append(sentence[:180].rstrip())
        if len(highlights) >= 5:
            break
    while len(highlights) < 3:
        fallback = "More details are available in the original source article."
        if fallback not in highlights:
            highlights.append(fallback)
        else:
            break
    story = {
        **item,
        "headline": clean_generated_text(item.get("title"))[:140].rstrip(),
        "summary": first_sentence(sentences[0]),
        "platform": infer_platform(item, source_text),
        "highlights": highlights[:5],
        "hashtags": [],
        "ai_fallback": True,
    }
    story["hashtags"] = default_hashtags(story)
    ok, errors = valid_story(story)
    if not ok:
        logger.warning("Deterministic fallback could not form a valid story: %s", errors)
        return None
    return story


def call_cerebras_batch(items_with_text: list[tuple[dict, str]]) -> dict[int, dict]:
    if not items_with_text or not CEREBRAS_API_KEY or Cerebras is None:
        return {}
    # One request per run keeps the bot far below the documented request-rate
    # limits and avoids long per-story retry cascades.
    client = Cerebras(api_key=CEREBRAS_API_KEY, max_retries=0, timeout=12.0)
    payload = []
    for idx, (item, article_text) in enumerate(items_with_text, 1):
        payload.append({
            "id": idx,
            "source": item["source"],
            "title": item["title"],
            "published_at": item["published_at"],
            "url": item["url"],
            "excerpt": item.get("excerpt", "")[:800],
            "article": safe_text(article_text)[:MAX_ARTICLE_CHARS_FOR_AI],
        })
    system_prompt = (
        "You are the writing assistant for the Telegram channel @GamingNewsroom. "
        "Format each trusted-source article into a concise gaming news post. "
        "Do not judge importance and do not reject an article. Use only facts present in the supplied source text. "
        "Never invent numbers, dates, prices, names, quotes, or claims. No Markdown or HTML in any field. "
        "Headline must be plain text without a leading #. Summary must be exactly one complete sentence. "
        "Choose one platform from PlayStation, Xbox, PC Game, Mobile Game. "
        "Write 3-5 factual highlights and 1-3 contextual hashtags. Return only the fields in the schema."
    )
    user_prompt = json.dumps({"articles": payload}, ensure_ascii=False)
    try:
        response = client.chat.completions.create(
            model=CEREBRAS_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "gaming_news_batch",
                    "strict": True,
                    "schema": STORY_SCHEMA,
                },
            },
            reasoning_effort="low",
            max_completion_tokens=2800,
            temperature=0.2,
        )
        content = response.choices[0].message.content or "{}"
        data = json.loads(content)
        output = {}
        for raw in data.get("posts") or []:
            if isinstance(raw, dict) and isinstance(raw.get("id"), int):
                output[int(raw["id"])] = raw
        logger.info("CEREBRAS: success | formatted=%d/%d", len(output), len(items_with_text))
        return output
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        logger.warning("CEREBRAS: unavailable | status=%s | %s | using source fallback", status, exc)
        return {}


def truncate_visible(text: str, limit: int = 32768) -> str:
    return text[:limit]


def rich_text_bold(text: str) -> dict:
    return {"type": "bold", "text": safe_text(text)}


def rich_text_url(text: str, url: str) -> dict:
    return {"type": "url", "text": safe_text(text), "url": safe_text(url)}


def rich_blocks(story: dict) -> list[dict]:
    headline = clean_generated_text(story["headline"])
    summary = first_sentence(story["summary"])
    platform = story["platform"]
    highlights = story["highlights"]
    hashtags = " ".join(story.get("hashtags") or default_hashtags(story))
    source = safe_text(story["source"]) or source_name_from_url(story["url"])
    source_url = story["url"]
    blocks: list[dict] = [
        {"type": "heading", "text": rich_text_bold(headline), "size": 2},
        {"type": "paragraph", "text": summary},
        {"type": "pullquote", "text": platform},
        {"type": "heading", "text": rich_text_bold("KEY HIGHLIGHTS"), "size": 3},
        {
            "type": "list",
            "items": [
                {"blocks": [{"type": "paragraph", "text": h}]} for h in highlights
            ],
        },
        {"type": "paragraph", "text": hashtags},
        {"type": "paragraph", "text": [rich_text_bold("Source: "), rich_text_url(source, source_url)]},
    ]
    return blocks


def build_rich_message(story: dict) -> dict:
    # Telegram Bot API rich messages are supported since 2026. This HTML is
    # retained as a simple compatibility representation. The photo is a
    # separate media block, per the Bot API specification.
    return {
        "blocks": [
            {"type": "paragraph", "text": ""},
        ] + rich_blocks(story),
        "skip_entity_detection": False,
    }


def find_font(bold: bool = False) -> str | None:
    paths = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ] if bold else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
    )
    for path in paths:
        if os.path.exists(path):
            return path
    return None


def download_image(url: str, referer: str) -> Image.Image | None:
    if not url:
        return None
    try:
        response = session.get(url, headers={**HEADERS, "Referer": referer}, timeout=8, stream=True)
        if response.status_code >= 400:
            return None
        ctype = response.headers.get("content-type", "").lower()
        if ctype and not ctype.startswith("image/"):
            return None
        buf = BytesIO()
        for chunk in response.iter_content(65536):
            if chunk:
                buf.write(chunk)
            if buf.tell() > 8_000_000:
                return None
        buf.seek(0)
        image = Image.open(buf)
        image.load()
        if image.width < 200 or image.height < 120:
            return None
        return image.convert("RGB")
    except Exception:
        return None


def cover(image: Image.Image, size: tuple[int, int] = (1200, 675)) -> Image.Image:
    tw, th = size
    ratio = max(tw / image.width, th / image.height)
    resized = image.resize((int(image.width * ratio), int(image.height * ratio)), Image.Resampling.LANCZOS)
    left = max(0, (resized.width - tw) // 2)
    top = max(0, (resized.height - th) // 2)
    return resized.crop((left, top, left + tw, top + th))


def add_channel_badge(image: Image.Image) -> Image.Image:
    canvas = cover(image).convert("RGBA")
    font_path = find_font(True)
    font = ImageFont.truetype(font_path, 24) if font_path else ImageFont.load_default()
    text = CHANNEL_USERNAME
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), text, font=font)
    pad_x, pad_y = 16, 8
    x2 = canvas.width - 18
    y2 = canvas.height - 16
    x1 = x2 - (bbox[2] - bbox[0]) - pad_x * 2
    y1 = y2 - (bbox[3] - bbox[1]) - pad_y * 2
    brightness = sum(canvas.resize((1, 1)).convert("RGB").getpixel((0, 0))) / 3
    if brightness < 128:
        bg, fg = (245, 245, 245, 225), (25, 25, 25, 255)
    else:
        bg, fg = (20, 24, 28, 220), (250, 250, 250, 255)
    draw.rounded_rectangle((x1, y1, x2, y2), radius=12, fill=bg)
    draw.text((x1 + pad_x, y1 + pad_y - 2), text, font=font, fill=fg)
    return canvas.convert("RGB")


def find_source_logo_url(source: str, domain: str) -> str:
    if domain in LOGO_URL_CACHE:
        return LOGO_URL_CACHE[domain]
    homepage = source_homepage(domain)
    found = ""
    try:
        response = session.get(homepage, headers=HEADERS, timeout=10)
        if response.status_code >= 400:
            LOGO_URL_CACHE[domain] = ""
            return ""
        soup = BeautifulSoup(response.text[:1_500_000], "html.parser")
        for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            stack = data if isinstance(data, list) else [data]
            while stack:
                obj = stack.pop()
                if isinstance(obj, dict):
                    logo = obj.get("logo")
                    if isinstance(logo, dict):
                        logo = logo.get("url")
                    if isinstance(logo, str) and logo:
                        found = urljoin(response.url, logo)
                        break
                    stack.extend(v for v in obj.values() if isinstance(v, (dict, list)))
                elif isinstance(obj, list):
                    stack.extend(obj)
            if found:
                break
        if not found:
            for attrs in ({"property": "og:logo"}, {"name": "og:logo"}):
                tag = soup.find("meta", attrs=attrs)
                if tag and safe_text(tag.get("content")):
                    found = urljoin(response.url, safe_text(tag["content"]))
                    break
        if not found:
            for rel in ("apple-touch-icon", "apple-touch-icon-precomposed", "icon"):
                tag = soup.find("link", rel=lambda v: v and rel in " ".join(v if isinstance(v, list) else [str(v)]).lower())
                if tag and safe_text(tag.get("href")):
                    found = urljoin(response.url, safe_text(tag["href"]))
                    break
    except Exception:
        found = ""
    LOGO_URL_CACHE[domain] = found
    return found


def source_logo_image(story: dict) -> Image.Image:
    logo_url = find_source_logo_url(story["source"], story["domain"])
    logo = download_image(logo_url, source_homepage(story["domain"])) if logo_url else None
    canvas = Image.new("RGB", (1200, 675), (28, 38, 50))
    if logo is not None:
        logo = logo.convert("RGBA")
        ratio = min(620 / logo.width, 320 / logo.height, 1.0)
        logo = logo.resize((max(1, int(logo.width * ratio)), max(1, int(logo.height * ratio))), Image.Resampling.LANCZOS)
        base = canvas.convert("RGBA")
        base.alpha_composite(logo, ((1200 - logo.width) // 2, 120))
        canvas = base.convert("RGB")
    else:
        font_path = find_font(True)
        font = ImageFont.truetype(font_path, 62) if font_path else ImageFont.load_default()
        draw = ImageDraw.Draw(canvas)
        name = safe_text(story["source"]) or "Source"
        bbox = draw.textbbox((0, 0), name, font=font)
        draw.text(((1200 - (bbox[2] - bbox[0])) // 2, 245), name, font=font, fill="white")
    return add_channel_badge(canvas)


def prepare_image(story: dict, index: int) -> str:
    image = download_image(story.get("image_url", ""), story["url"])
    if image is None:
        image = source_logo_image(story)
        story["image_fallback"] = "source_logo_or_name"
        logger.info("IMAGE: source fallback | %s", story["source"])
    else:
        image = add_channel_badge(image)
        story["image_fallback"] = "article_or_og"
    path = f"/tmp/gaming_news_{index}.jpg"
    image.save(path, "JPEG", quality=90, optimize=True)
    return path


def telegram_request(method: str, data: dict, files: dict | None = None, attempts: int = 2) -> dict:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    last = {"ok": False, "description": "unknown"}
    for attempt in range(attempts):
        try:
            response = session.post(url, data=data, files=files, timeout=25)
            try:
                result = response.json()
            except Exception:
                result = {"ok": False, "description": response.text[:500]}
            if result.get("ok"):
                return result
            last = result
            if response.status_code >= 500 and attempt + 1 < attempts:
                time.sleep(0.5)
                continue
            if response.status_code == 429 and attempt + 1 < attempts:
                retry_after = min(int((result.get("parameters") or {}).get("retry_after", 1)), 5)
                logger.warning("TELEGRAM 429 | retry_after=%ss", retry_after)
                time.sleep(retry_after)
                continue
            break
        except Exception as exc:
            last = {"ok": False, "description": str(exc)}
            if attempt + 1 < attempts:
                time.sleep(0.5)
    return last


def telegram_fallback_caption(story: dict) -> str:
    headline = clean_generated_text(story["headline"])[:100]
    summary = first_sentence(story["summary"])[:180]
    platform = story["platform"]
    usable_highlights = [clean_generated_text(h)[:110] for h in story["highlights"]]
    hashtags = " ".join(story.get("hashtags") or default_hashtags(story))[:90]
    source = safe_text(story["source"])[:60]
    url = safe_text(story["url"])
    # Keep the fallback caption within Telegram's 1024-character photo-caption limit
    # without truncating the HTML itself.
    while usable_highlights:
        lines = "\n".join(f"• {html.escape(h)}" for h in usable_highlights)
        caption = (
            f"<b>{html.escape(headline)}</b>\n\n"
            f"{html.escape(summary)}\n\n"
            f"<blockquote>{html.escape(platform)}</blockquote>\n\n"
            f"<b>KEY HIGHLIGHTS</b>\n\n"
            f"{lines}\n\n"
            f"{html.escape(hashtags)}\n\n"
            f"<b>Source:</b> <a href=\"{html.escape(url, quote=True)}\">{html.escape(source)}</a>"
        )
        if len(caption) <= 1024 or len(usable_highlights) == 3:
            return caption
        usable_highlights.pop()
    return (
        f"<b>{html.escape(headline)}</b>\n\n{html.escape(summary)}\n\n"
        f"<blockquote>{html.escape(platform)}</blockquote>\n\n"
        f"{html.escape(hashtags)}\n\n"
        f"<b>Source:</b> <a href=\"{html.escape(url, quote=True)}\">{html.escape(source)}</a>"
    )


def send_story(story: dict, image_path: str) -> tuple[bool, dict]:
    if not TELEGRAM_BOT_TOKEN:
        return False, {"ok": False, "description": "missing TELEGRAM_BOT_TOKEN"}

    blocks = [
        {"type": "photo", "photo": {"type": "photo", "media": "attach://photo"}},
        *rich_blocks(story),
    ]
    rich_message = json.dumps({"blocks": blocks, "skip_entity_detection": False}, ensure_ascii=False)
    with open(image_path, "rb") as photo:
        result = telegram_request(
            "sendRichMessage",
            data={"chat_id": TELEGRAM_CHANNEL, "rich_message": rich_message},
            files={"photo": photo},
            attempts=2,
        )
    if result.get("ok"):
        return True, result

    # Compatibility fallback: standard sendPhoto still publishes the complete
    # structure in a single message when Rich Messages are temporarily unavailable.
    logger.warning("TELEGRAM: RichMessage failed; using sendPhoto fallback | %s", result.get("description"))
    with open(image_path, "rb") as photo:
        fallback = telegram_request(
            "sendPhoto",
            data={
                "chat_id": TELEGRAM_CHANNEL,
                "photo": "attach://photo",
                "caption": telegram_fallback_caption(story),
                "parse_mode": "HTML",
            },
            files={"photo": photo},
            attempts=2,
        )
    return bool(fallback.get("ok")), fallback


def ensure_runtime_requirements() -> bool:
    missing = []
    if feedparser is None:
        missing.append("feedparser")
    if trafilatura is None:
        missing.append("trafilatura")
    if Cerebras is None:
        missing.append("cerebras_cloud_sdk")
    if missing:
        logger.error("Missing Python packages: %s", ", ".join(missing))
        return False
    return True


def prepare_candidates(rss_items: list[dict], posted: set[str]) -> list[dict]:
    fresh = [x for x in rss_items if canonical_url(x.get("url", "")) not in posted]
    return dedup_items(fresh)


def run_once(dry_run: bool = False) -> int:
    started = time.time()
    state = load_state()
    posted = load_posted_urls()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)

    rss_items = collect_rss(state, cutoff)
    candidates = prepare_candidates(rss_items, posted)
    exa_used = False

    if len(candidates) < PUBLISH_TARGET and MAX_EXA_QUERIES_PER_RUN > 0:
        missing = PUBLISH_TARGET - len(candidates)
        exa_used = True
        exa_items = exa_search_fallback(cutoff, missing)
        candidates = dedup_items(candidates + [x for x in exa_items if canonical_url(x.get("url", "")) not in posted])

    candidates = sorted(
        candidates,
        key=lambda x: parse_date(x.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:CANDIDATE_POOL_SIZE]

    logger.info(
        "SELECTION: rss_new=%d | candidates=%d | pool=%d | exa=%s",
        len(prepare_candidates(rss_items, posted)), len(candidates), CANDIDATE_POOL_SIZE, exa_used,
    )

    if not candidates:
        state["last_run"] = iso_now()
        state["stats"] = {
            "rss_recent": len(rss_items),
            "new_candidates": 0,
            "exa_used": exa_used,
            "candidates": 0,
            "stories_ready": 0,
            "published": 0,
            "status": "NO_NEWS",
            "duration_seconds": round(time.time() - started, 2),
        }
        save_state(state)
        logger.info("RUN STATUS=NO_NEWS | Published=0/%d", PUBLISH_TARGET)
        return 0

    article_pairs: list[tuple[dict, str]] = []
    for item in candidates:
        article_text, image_url = extract_article(item)
        item = dict(item)
        item["image_url"] = image_url or item.get("image_url", "")
        item["article_text"] = article_text[:MAX_ARTICLE_CHARS_FOR_AI]
        article_pairs.append((item, article_text))

    ai_raw = call_cerebras_batch(article_pairs)
    stories: list[dict] = []
    ai_successes = 0
    fallbacks = 0
    for idx, (item, article_text) in enumerate(article_pairs, 1):
        raw = ai_raw.get(idx)
        story = sanitize_ai_story(raw, item) if raw else None
        if story:
            ai_successes += 1
        else:
            story = build_deterministic_story(item, article_text)
            if story:
                fallbacks += 1
        if story:
            stories.append(story)

    stories = stories[:PUBLISH_TARGET]
    logger.info(
        "STORIES READY: %d/%d | Cerebras=%d | fallback=%d",
        len(stories), len(candidates), ai_successes, fallbacks,
    )

    published = 0
    failures = 0
    for idx, story in enumerate(stories, 1):
        image_path = prepare_image(story, idx)
        if dry_run:
            logger.info("DRY RUN #%d | %s | %s | %s", idx, story["headline"], story["platform"], story["source"])
            continue
        ok, result = send_story(story, image_path)
        if ok:
            published += 1
            append_posted_url(story["url"])
            logger.info("PUBLISHED #%d | %s | %s", published, story["source"], story["headline"])
        else:
            failures += 1
            logger.error("TELEGRAM FAILED #%d | %s", idx, result.get("description"))

    status = "HEALTHY" if published == min(PUBLISH_TARGET, len(stories)) and not dry_run else (
        "DEGRADED" if published > 0 else "NO_PUBLISH"
    )
    state["last_run"] = iso_now()
    state["stats"] = {
        "rss_recent": len(rss_items),
        "new_candidates": len(prepare_candidates(rss_items, posted)),
        "candidates": len(candidates),
        "exa_used": exa_used,
        "stories_ready": len(stories),
        "cerebras_successes": ai_successes,
        "deterministic_fallbacks": fallbacks,
        "published": published,
        "telegram_failures": failures,
        "dry_run": dry_run,
        "status": status,
        "duration_seconds": round(time.time() - started, 2),
    }
    save_state(state)
    logger.info(
        "RUN STATUS=%s | Published=%d/%d | RSS=%d | Exa=%s | Cerebras=%d | Fallback=%d | Duration=%.1fs",
        status, published, PUBLISH_TARGET, len(rss_items), exa_used, ai_successes, fallbacks, time.time() - started,
    )
    return published


def self_test() -> int:
    logger.info("SELF-TEST: Gaming News")
    assert len(TRUSTED_SOURCES) == 20
    assert WINDOW_HOURS == 24
    assert PUBLISH_TARGET == 6
    assert CANDIDATE_POOL_SIZE == 12
    assert MAX_CEREBRAS_CALLS_PER_RUN == 1

    items = []
    for i, (source, domain, platform_hint) in enumerate([
        ("IGN", "ign.com", "playstation"),
        ("GameSpot", "gamespot.com", "xbox"),
        ("VGC", "videogameschronicle.com", "pc"),
        ("Nintendo Life", "nintendolife.com", "switch"),
        ("PC Gamer", "pcgamer.com", "steam"),
        ("Push Square", "pushsquare.com", "playstation"),
        ("Pure Xbox", "purexbox.com", "xbox"),
    ], 1):
        items.append({
            "title": f"{source} reports a major {platform_hint} gaming announcement number {i}",
            "url": f"https://{domain}/story-{i}",
            "source": source,
            "domain": domain,
            "published_at": f"2026-09-27T{10+i:02d}:00:00+00:00",
            "excerpt": f"{source} reports a new gaming development for {platform_hint}. Players can learn more in the original report. The article provides additional details about the announcement.",
            "image_url": "",
        })

    duplicate = dict(items[0])
    duplicate["url"] = "https://IGN.com/story-1?utm_source=test"
    duplicate["title"] = items[0]["title"]
    deduped = dedup_items(items + [duplicate])
    assert len(deduped) == len(items)

    story = build_deterministic_story(
        deduped[0],
        "Minecraft announced a new gaming feature. Players will be able to explore the new content in the latest release. More details are available in the original article.",
    )
    assert story is not None
    assert 3 <= len(story["highlights"]) <= 5
    assert story["platform"] in PLATFORMS
    assert story["hashtags"]

    blocks = rich_blocks(story)
    blob = json.dumps(blocks, ensure_ascii=False)
    assert not story["headline"].startswith("#")
    assert all(not any(mark in text for mark in ("**", "__", "`")) for text in [story["headline"], story["summary"], *story["highlights"]])

    rich = {"blocks": [{"type": "photo", "photo": {"type": "photo", "media": "attach://photo"}}, *blocks]}
    assert rich["blocks"][0]["type"] == "photo"
    assert rich["blocks"][0]["photo"]["media"] == "attach://photo"
    assert any(block.get("type") == "pullquote" for block in blocks)
    assert any(block.get("type") == "heading" and block.get("size") == 3 for block in blocks)

    # Seven source-derived stories can be formed without any AI at all.
    fallback_stories = [build_deterministic_story(item, item["excerpt"]) for item in deduped]
    assert sum(1 for x in fallback_stories if x) >= 6

    # AI outage simulation: empty key must immediately select source fallback, not retry.
    saved_key = globals()["CEREBRAS_API_KEY"]
    globals()["CEREBRAS_API_KEY"] = ""
    assert call_cerebras_batch([(deduped[0], deduped[0]["excerpt"])]) == {}
    globals()["CEREBRAS_API_KEY"] = saved_key

    logger.info("SELF-TEST PASSED: RSS, dedup, 6-story fallback, output structure, AI outage path")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not ensure_runtime_requirements():
        return 2
    if not TELEGRAM_BOT_TOKEN:
        logger.error("Missing required environment variable: TELEGRAM_BOT_TOKEN")
        return 2
    if not CEREBRAS_API_KEY:
        logger.warning("CEREBRAS_API_KEY is not set; source-derived fallback will be used")
    if not EXA_API_KEY:
        logger.warning("EXA_API_KEY is not set; RSS-only mode will be used")
    try:
        run_once(dry_run=args.dry_run)
        return 0
    except Exception:
        logger.exception("FATAL RUN ERROR")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
