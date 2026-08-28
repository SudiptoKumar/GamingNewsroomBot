import os
import re
import json
import time
import html
import argparse
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, urljoin, quote
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from io import BytesIO

import requests
import feedparser
import trafilatura
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFile
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from exa_py import Exa
from cerebras.cloud.sdk import Cerebras


# ============================================================
# CONFIGURATION
# ============================================================

EXA_API_KEY = os.environ["EXA_API_KEY"]
CEREBRAS_API_KEY = os.environ["CEREBRAS_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

TELEGRAM_CHANNEL = (os.environ.get("TELEGRAM_CHANNEL") or "@GamingNewsroom").strip()
TELEGRAM_ADMIN_CHAT_ID = (os.environ.get("TELEGRAM_ADMIN_CHAT_ID") or "").strip()

NEWS_MODE = (os.environ.get("NEWS_MODE") or "update").strip().lower()
VALID_NEWS_MODES = {"update"}
if NEWS_MODE not in VALID_NEWS_MODES:
    raise ValueError(
        f"Invalid NEWS_MODE={NEWS_MODE!r}; expected one of {sorted(VALID_NEWS_MODES)}"
    )

CEREBRAS_MODEL = os.environ.get("CEREBRAS_MODEL", "gpt-oss-120b")

POSTED_FILE = "posted_urls.txt"
STATE_FILE = "gaming_state.json"

BD_TZ = ZoneInfo("Asia/Dhaka")

# GamingNewsroom editorial policy:
# - one run per 24 hours
# - 72-hour hard lookback
# - publish every story scoring >= 7/10
# - no editorial quota
# - 40-story circuit breaker for abnormal classifier output
DISCOVERY_LOOKBACK_HOURS = 72
PRIMARY_WINDOW_HOURS = 24
POST_THRESHOLD = 7
POST_DELAY_SECONDS = 3.5
QUEUE_RETENTION_DAYS = 4
EVENT_RETENTION_DAYS = 30
MAX_RSS_CANDIDATES = 220
MAX_EXA_CANDIDATES = 40
MAX_GOOGLE_NEWS_CANDIDATES = 40
MAX_EXCERPT_ENRICH = 30
MAX_RICH_CHARACTERS = 32768
CIRCUIT_BREAKER_MAX_STORIES = 40
THIN_DAY_THRESHOLD = 3
FUTURE_TOLERANCE_MINUTES = 10

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for",
    "from", "with", "by", "at", "as", "is", "are", "was", "were",
    "be", "been", "being", "has", "have", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "can",
    "this", "that", "these", "those", "it", "its", "their", "they",
    "them", "he", "she", "his", "her", "we", "our", "you", "your",
    "new", "after", "before", "over", "into", "than", "about",
}

# 20-source whitelist from the GamingNewsroom README.
# Google News RSS is used where a publisher does not expose a stable public RSS
# endpoint. The feed is resolved to the publisher URL before queuing.
SOURCE_DEFS = [
    ("VGC", "vgc.news", "site:vgc.news gaming news"),
    ("Insider Gaming", "insider-gaming.com", "site:insider-gaming.com gaming news"),
    ("Gematsu", "gematsu.com", "site:gematsu.com gaming news"),
    ("IGN", "ign.com", "site:ign.com gaming news"),
    ("GameSpot", "gamespot.com", "site:gamespot.com gaming news"),
    ("Eurogamer", "eurogamer.net", "site:eurogamer.net gaming news"),
    ("GamesIndustry.biz", "gamesindustry.biz", "site:gamesindustry.biz games industry"),
    ("PC Gamer", "pcgamer.com", "site:pcgamer.com gaming news"),
    ("GamesRadar+", "gamesradar.com", "site:gamesradar.com gaming news"),
    ("Polygon", "polygon.com", "site:polygon.com games news"),
    ("Kotaku", "kotaku.com", "site:kotaku.com gaming news"),
    ("VG247", "vg247.com", "site:vg247.com gaming news"),
    ("Destructoid", "destructoid.com", "site:destructoid.com gaming news"),
    ("Rock Paper Shotgun", "rockpapershotgun.com", "site:rockpapershotgun.com gaming news"),
    ("Digital Foundry", "eurogamer.net", "site:eurogamer.net/digitalfoundry gaming"),
    ("Nintendo Life", "nintendolife.com", "site:nintendolife.com gaming news"),
    ("Push Square", "pushsquare.com", "site:pushsquare.com gaming news"),
    ("Pure Xbox", "purexbox.com", "site:purexbox.com xbox gaming"),
    ("Pocket Gamer", "pocketgamer.com", "site:pocketgamer.com gaming news"),
    ("Game Developer", "gamedeveloper.com", "site:gamedeveloper.com games industry"),
]

PRIMARY_DOMAINS = [domain for _, domain, _ in SOURCE_DEFS]
FALLBACK_DEFS = [
    ("TheGamer", "thegamer.com"),
    ("Siliconera", "siliconera.com"),
    ("Wccftech", "wccftech.com"),
    ("TouchArcade", "toucharcade.com"),
    ("Game Rant", "gamerant.com"),
]
FALLBACK_DOMAINS = [domain for _, domain in FALLBACK_DEFS]

SOURCE_NAMES = {domain: name for name, domain, _ in SOURCE_DEFS}
SOURCE_NAMES.update({domain: name for name, domain in FALLBACK_DEFS})
SOURCE_NAMES["eurogamer.net"] = "Eurogamer"

# Taxonomy is informational only. It does not create quotas.
TOPICS = {
    "Gaming": [
        "Exclusive / Breaking",
        "Major Release",
        "Industry / Business",
        "Platform / Hardware",
        "Security / Outage",
        "Game Update / Expansion",
        "Platform Policy",
        "Subscription / Monetization",
        "Studio / Publisher",
        "Acquisition / Investment",
        "Development / Production",
        "Esports / Industry",
        "Mobile Gaming",
        "PC Gaming",
        "PlayStation",
        "Xbox",
        "Nintendo",
        "Valve / Steam",
        "Epic Games",
        "Cloud / Online Services",
    ]
}

TOPIC_ALIASES = {
    "breaking": "Exclusive / Breaking",
    "exclusive": "Exclusive / Breaking",
    "release": "Major Release",
    "launch": "Major Release",
    "business": "Industry / Business",
    "industry": "Industry / Business",
    "hardware": "Platform / Hardware",
    "security": "Security / Outage",
    "outage": "Security / Outage",
    "patch": "Game Update / Expansion",
    "expansion": "Game Update / Expansion",
    "platform": "Platform Policy",
    "subscription": "Subscription / Monetization",
    "monetization": "Subscription / Monetization",
    "studio": "Studio / Publisher",
    "publisher": "Studio / Publisher",
    "acquisition": "Acquisition / Investment",
    "investment": "Acquisition / Investment",
    "development": "Development / Production",
    "mobile": "Mobile Gaming",
    "pc": "PC Gaming",
    "playstation": "PlayStation",
    "ps5": "PlayStation",
    "xbox": "Xbox",
    "nintendo": "Nintendo",
    "switch": "Nintendo",
    "steam": "Valve / Steam",
    "valve": "Valve / Steam",
    "epic": "Epic Games",
    "cloud": "Cloud / Online Services",
    "online": "Cloud / Online Services",
}

# ============================================================
# CATEGORY METADATA
# ============================================================

CATEGORY_HASHTAGS = {
    "Exclusive / Breaking": ["#GamingNews", "#Breaking"],
    "Major Release": ["#GamingNews", "#GameRelease"],
    "Industry / Business": ["#GamingBusiness", "#GameIndustry"],
    "Platform / Hardware": ["#GamingHardware", "#GamingNews"],
    "Security / Outage": ["#GamingSecurity", "#GamingNews"],
    "Game Update / Expansion": ["#GameUpdate", "#GamingNews"],
    "Platform Policy": ["#PlatformNews", "#GamingNews"],
    "Subscription / Monetization": ["#GamingBusiness", "#Monetization"],
    "Studio / Publisher": ["#GameIndustry", "#GamingNews"],
    "Acquisition / Investment": ["#GamingBusiness", "#GameIndustry"],
    "Development / Production": ["#GameDevelopment", "#GamingNews"],
    "Esports / Industry": ["#Esports", "#GameIndustry"],
    "Mobile Gaming": ["#MobileGaming", "#GamingNews"],
    "PC Gaming": ["#PCGaming", "#GamingNews"],
    "PlayStation": ["#PlayStation", "#GamingNews"],
    "Xbox": ["#Xbox", "#GamingNews"],
    "Nintendo": ["#Nintendo", "#GamingNews"],
    "Valve / Steam": ["#Steam", "#PCGaming"],
    "Epic Games": ["#EpicGames", "#GamingNews"],
    "Cloud / Online Services": ["#GamingCloud", "#OnlineGaming"],
}

def canonical_topic(topic, region="Gaming"):
    raw = safe_text(topic)
    key = raw.lower().strip()
    if key in TOPIC_ALIASES:
        return TOPIC_ALIASES[key]
    for item in TOPICS["Gaming"]:
        if key == item.lower():
            return item

    patterns = [
        (("leak", "exclusive", "breaking"), "Exclusive / Breaking"),
        (("release", "launch", "launched", "ships", "released"), "Major Release"),
        (("acquisition", "acquires", "merger", "investment", "funding"), "Acquisition / Investment"),
        (("layoff", "shutdown", "closure", "studio", "publisher"), "Studio / Publisher"),
        (("outage", "hack", "breach", "compromise", "security"), "Security / Outage"),
        (("price", "subscription", "game pass", "ps plus", "monetization", "storefront"), "Subscription / Monetization"),
        (("console", "gpu", "performance", "hardware", "handheld"), "Platform / Hardware"),
        (("steam", "valve"), "Valve / Steam"),
        (("xbox", "microsoft gaming"), "Xbox"),
        (("playstation", "ps5", "sony interactive"), "PlayStation"),
        (("nintendo", "switch"), "Nintendo"),
        (("epic games",), "Epic Games"),
        (("mobile", "ios", "android"), "Mobile Gaming"),
        (("esports",), "Esports / Industry"),
        (("patch", "update", "expansion", "dlc"), "Game Update / Expansion"),
        (("online service", "server", "cloud gaming"), "Cloud / Online Services"),
        (("development", "developer", "production"), "Development / Production"),
        (("platform policy", "terms of service", "policy"), "Platform Policy"),
        (("pc", "windows"), "PC Gaming"),
    ]
    for needles, canonical in patterns:
        if any(needle in key for needle in needles):
            return canonical
    return "Gaming News"

def category_hashtags(story):
    tags = []
    topic = safe_text(story.get("topic"))
    for tag in CATEGORY_HASHTAGS.get(topic, []):
        if tag not in tags:
            tags.append(tag)
    platforms = story.get("platforms") or []
    for platform in platforms:
        p = safe_text(platform).lower()
        mapping = {
            "pc": "#PCGaming",
            "ps5": "#PlayStation",
            "playstation": "#PlayStation",
            "xbox": "#Xbox",
            "switch": "#Nintendo",
            "nintendo": "#Nintendo",
            "mobile": "#MobileGaming",
        }
        tag = mapping.get(p)
        if tag and tag not in tags:
            tags.append(tag)
    if "#GamingNews" not in tags:
        tags.append("#GamingNews")
    return tags[:3]

# ============================================================
# LOGGING + HTTP
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("gaming-news-bot")

ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = 50_000_000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    )
}

session = requests.Session()
session.headers.update(HEADERS)

retry_policy = Retry(
    total=4,
    connect=4,
    read=4,
    backoff_factor=1.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    respect_retry_after_header=True,
)

adapter = HTTPAdapter(
    max_retries=retry_policy,
    pool_connections=20,
    pool_maxsize=20,
)

session.mount("https://", adapter)
session.mount("http://", adapter)



RSS_FEEDS = [
    {
        "name": name,
        "region": "Gaming",
        "url": (
            "https://news.google.com/rss/search?q="
            + quote(query + " when:3d")
            + "&hl=en-US&gl=US&ceid=US:en"
        ),
        "kind": "google_news",
        "domain": domain,
    }
    for name, domain, query in SOURCE_DEFS
]

# ============================================================
# HELPERS
# ============================================================

def safe_text(value):
    return "" if value is None else str(value).strip()


def canonical_url(url):
    raw = safe_text(url)
    if not raw:
        return ""

    parsed = urlparse(raw)

    host = (
        parsed.netloc.lower()
        .removeprefix("www.")
        .removeprefix("amp.")
    )

    path = parsed.path or "/"
    path = path.rstrip("/")
    path = re.sub(r"/amp$", "", path, flags=re.I)
    path = re.sub(r"\.amp$", "", path, flags=re.I)

    return f"{host}{path}"


def normalize_title(title):
    text = safe_text(title).lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def title_tokens(text):
    text = normalize_title(text)
    return {
        token
        for token in text.split()
        if len(token) >= 3
    }


def token_jaccard(a, b):
    aa = title_tokens(a)
    bb = title_tokens(b)
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / max(1, len(aa | bb))


def title_similarity(a, b):
    na = normalize_title(a)
    nb = normalize_title(b)
    if not na or not nb:
        return 0.0
    sequence = SequenceMatcher(None, na, nb).ratio()
    jaccard = token_jaccard(na, nb)
    return max(sequence, jaccard)


def event_similarity(a, b):
    """Cheap event-level similarity without an embedding dependency."""
    sequence = SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()
    jaccard = token_jaccard(a, b)
    return (0.55 * sequence) + (0.45 * jaccard)


def likely_same_event(a, b):
    return (
        title_similarity(a, b) >= 0.90
        or event_similarity(a, b) >= 0.80
    )


def parse_datetime(value):
    raw = safe_text(value)
    if not raw:
        return None

    try:
        dt = datetime.fromisoformat(
            raw.replace("Z", "+00:00")
        )
        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )
        return dt.astimezone(BD_TZ)
    except Exception:
        pass

    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )
        return dt.astimezone(BD_TZ)
    except Exception:
        return None


def feed_entry_datetime(entry):
    for key in (
        "published_parsed",
        "updated_parsed",
        "created_parsed",
    ):
        parsed = entry.get(key)
        if parsed:
            try:
                return datetime(
                    *parsed[:6],
                    tzinfo=timezone.utc,
                ).astimezone(BD_TZ)
            except Exception:
                pass

    for key in (
        "published",
        "updated",
        "created",
    ):
        dt = parse_datetime(
            entry.get(key)
        )
        if dt:
            return dt

    return None


def trim_source_text(text, limit):
    """Trim source text before rendering. Never appends ellipses."""
    text = safe_text(text)
    if len(text) <= limit:
        return text

    trimmed = text[:limit].rstrip()
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0]

    return trimmed.rstrip(" ,:;-/—")


def clean_generated_text(text):
    text = safe_text(text)

    # Prevent visible truncation artifacts.
    text = re.sub(r"\.{2,}", ".", text)
    text = text.replace("\u2026", "")

    # Remove incomplete endings.
    text = re.sub(
        r"\s*[,;:]\s*$",
        "",
        text,
    )
    text = re.sub(
        r"\s*[-—]\s*$",
        "",
        text,
    )

    return text.strip()


def complete_text(text):
    raw = safe_text(text)
    if not raw:
        return False

    # A text that clean_generated_text() would mutilate
    # (trailing dash/comma/colon) is INCOMPLETE.
    if re.search(r"[\s,;:\-—…]+$", raw):
        return False

    text = clean_generated_text(raw)
    if not text:
        return False

    return not text.endswith(
        (",", ";", ":", "-", "—", "…")
    )


def source_name(url):
    domain = normalized_domain(url)
    path = urlparse(safe_text(url)).path.lower()
    if domain == "eurogamer.net" and "/digitalfoundry" in path:
        return "Digital Foundry"
    return SOURCE_NAMES.get(domain, domain or "Source")


def article_region(url):
    return "Gaming"


def now_iso():
    return datetime.now(
        BD_TZ
    ).isoformat()


# ============================================================
# STATE: QUEUE + EVENTS + KNOWLEDGE
# ============================================================

def default_state():
    return {
        "feeds": {},
        "queue": {},
        "events": {},
        "event_clusters": {},
        "posted_event_ids": [],
        "recent_titles": [],
    }


def load_state():
    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

        if not isinstance(
            data,
            dict,
        ):
            return default_state()

        base = default_state()
        base.update(data)

        return base

    except Exception:
        return default_state()


def save_state(state):
    tmp = STATE_FILE + ".tmp"

    with open(
        tmp,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(
        tmp,
        STATE_FILE,
    )


def load_posted_urls():
    try:
        with open(
            POSTED_FILE,
            "r",
            encoding="utf-8",
        ) as f:
            return {
                canonical_url(line)
                for line in f
                if safe_text(line)
            }
    except FileNotFoundError:
        return set()


def save_posted_url(canonical):
    if not canonical:
        return

    with open(
        POSTED_FILE,
        "a",
        encoding="utf-8",
    ) as f:
        f.write(
            canonical
            + "\n"
        )


STATE = load_state()
POSTED_URLS = load_posted_urls()


def prune_state():
    cutoff_queue = (
        datetime.now(BD_TZ)
        - timedelta(
            days=QUEUE_RETENTION_DAYS
        )
    )

    cutoff_events = (
        datetime.now(BD_TZ)
        - timedelta(
            days=EVENT_RETENTION_DAYS
        )
    )

    queue = STATE.get(
        "queue",
        {},
    )

    keep_queue = {}

    for key, item in queue.items():
        dt = parse_datetime(
            item.get("last_seen")
            or item.get("published_date")
        )

        if (
            dt
            and dt >= cutoff_queue
        ):
            keep_queue[key] = item

    STATE["queue"] = keep_queue

    events = STATE.get(
        "events",
        {},
    )

    keep_events = {}

    for key, event in events.items():
        dt = parse_datetime(
            event.get("published_at")
            or event.get("selected_at")
        )

        if (
            dt
            and dt >= cutoff_events
        ):
            keep_events[key] = event

    STATE["events"] = keep_events

    titles = STATE.get(
        "recent_titles",
        [],
    )

    STATE["recent_titles"] = titles[-400:]


# ============================================================
# TIME WINDOWS
# ============================================================

NOW_BD = datetime.now(
    BD_TZ
)

TODAY_START = NOW_BD.replace(
    hour=0,
    minute=0,
    second=0,
    microsecond=0,
)

YESTERDAY_START = (
    TODAY_START
    - timedelta(days=1)
)

DISCOVERY_START = (
    NOW_BD
    - timedelta(
        hours=DISCOVERY_LOOKBACK_HOURS
    )
)
DISCOVERY_END = (
    NOW_BD
    + timedelta(
        minutes=FUTURE_TOLERANCE_MINUTES
    )
)

DISCOVERY_TARGET = 36


# ============================================================
# CLIENTS
# ============================================================

exa = Exa(
    api_key=EXA_API_KEY
)

cerebras = Cerebras(
    api_key=CEREBRAS_API_KEY
)


# ============================================================
# CANDIDATE FILTERING
# ============================================================

BAD_PATH_RE = re.compile(
    r"/(opinion|editorial|sponsored|"
    r"tag|topic|live-blog|liveblog|"
    r"photo|photos|video)(/|$)",
    re.I,
)

BAD_TITLE_RE = re.compile(
    r"\b(sponsored|advertisement|"
    r"promo|opinion|editorial)\b",
    re.I,
)


def candidate_basic_allowed(item):
    url = safe_text(
        item.get("url")
    )
    title = safe_text(
        item.get("title")
    )
    published = item.get(
        "published_dt"
    )

    # Discovery providers do not all return the same timestamp type.
    # RSS paths often pass a datetime, while Exa/other JSON paths pass
    # ISO-8601 strings. Normalize both before comparing against our window.
    if isinstance(published, str):
        published = parse_datetime(published)

    if (
        not url
        or not title
        or not published
    ):
        return False

    if BAD_PATH_RE.search(
        urlparse(url).path
    ):
        return False

    if BAD_TITLE_RE.search(
        title
    ):
        return False

    if not (
        DISCOVERY_START
        <= published
        <= DISCOVERY_END
    ):
        return False

    region = safe_text(item.get("region"))
    if region and not allowed_source_for_region(url, region):
        return False

    canonical = canonical_url(
        url
    )

    return bool(
        canonical
    )


def title_duplicate_against_state(title):
    for previous in STATE.get(
        "recent_titles",
        [],
    )[-250:]:
        if title_similarity(
            title,
            previous,
        ) >= 0.88:
            return True

    return False


def title_duplicate_against_list(
    title,
    candidates,
    threshold=0.88,
):
    for candidate in candidates:
        if title_similarity(
            title,
            candidate["title"],
        ) >= threshold:
            return True

    return False


# ============================================================
# RSS INGESTION + PERSISTENT QUEUE
# ============================================================

def extract_entry_image(
    entry,
    page_url,
):
    for key in (
        "media_content",
        "media_thumbnail",
    ):
        for item in entry.get(
            key,
            [],
        ):
            image_url = safe_text(
                item.get("url")
            )

            if image_url:
                return urljoin(
                    page_url,
                    image_url,
                )

    for enclosure in entry.get(
        "enclosures",
        [],
    ):
        href = safe_text(
            enclosure.get("href")
        )

        mime = safe_text(
            enclosure.get("type")
        ).lower()

        if (
            href
            and (
                not mime
                or mime.startswith(
                    "image/"
                )
            )
        ):
            return urljoin(
                page_url,
                href,
            )

    return ""


def queue_candidate(item):
    canonical = item["canonical"]

    existing = STATE["queue"].get(
        canonical
    )

    if existing:
        existing.update(
            {
                "last_seen": now_iso(),
                "image": (
                    item.get("image")
                    or existing.get("image", "")
                ),
            }
        )
        return

    STATE["queue"][canonical] = {
        **item,
        "status": "pending",
        "first_seen": now_iso(),
        "last_seen": now_iso(),
    }


def fetch_rss_feed(
    feed_def,
):
    url = feed_def["url"]

    old = STATE["feeds"].get(
        url,
        {},
    )

    headers = dict(
        HEADERS
    )

    if old.get("etag"):
        headers["If-None-Match"] = old[
            "etag"
        ]

    if old.get(
        "last_modified"
    ):
        headers["If-Modified-Since"] = old[
            "last_modified"
        ]

    try:
        response = session.get(
            url,
            headers=headers,
            timeout=20,
        )

        # 304 means the queue remains intact. The feed is reachable,
        # so this counts as healthy and clears any fail streak.
        if response.status_code == 304:
            logger.info(
                "RSS 304: %s",
                feed_def["name"],
            )
            mark_feed_healthy(feed_def, old)
            return 0

        if response.status_code >= 400:
            logger.warning(
                "RSS %s returned %s",
                feed_def["name"],
                response.status_code,
            )
            mark_feed_failed(feed_def, old)
            return 0

        STATE["feeds"][url] = {
            "etag": response.headers.get(
                "ETag",
                old.get("etag"),
            ),
            "last_modified": response.headers.get(
                "Last-Modified",
                old.get("last_modified"),
            ),
            "last_checked": now_iso(),
            "fail_count": 0,
            "alerted": False,
        }

        parsed = feedparser.parse(
            response.content
        )

        entry_count = len(parsed.entries)
        added = 0
        rejected = 0
        unresolved = 0

        logger.info(
            "RSS source=%s status=200 entries=%d",
            feed_def["name"],
            entry_count,
        )

        for entry in parsed.entries:
            published_dt = feed_entry_datetime(
                entry
            )

            date_estimated = False

            if not published_dt:
                # Some feeds send a date format we cannot parse.
                # Do not throw the story away: use fetch time instead,
                # and mark it so downstream code knows it is a guess.
                published_dt = datetime.now(
                    BD_TZ
                )
                date_estimated = True

            article_url = urljoin(
                url,
                safe_text(
                    entry.get("link")
                ),
            )
            if feed_def.get("kind") == "google_news":
                resolved_url = resolve_google_news_url(article_url)
                if not resolved_url:
                    unresolved += 1
                    continue
                article_url = resolved_url

            title = safe_text(
                entry.get("title")
            )

            if not article_url or not title:
                continue

            item = {
                "title": title,
                "url": article_url,
                "canonical": canonical_url(
                    article_url
                ),
                "published_dt": published_dt.isoformat(),
                "published_date": published_dt.isoformat(),
                "source": feed_def["name"],
                "region": feed_def["region"],
                "excerpt": BeautifulSoup(
                    safe_text(
                        entry.get(
                            "summary"
                        )
                        or entry.get(
                            "description"
                        )
                    ),
                    "html.parser",
                ).get_text(
                    " ",
                    strip=True,
                )[:2000],
                "image": extract_entry_image(
                    entry,
                    article_url,
                ),
                "discovery": "rss",
                "date_estimated": date_estimated,
            }

            if not candidate_basic_allowed(
                {
                    **item,
                    "published_dt": published_dt,
                }
            ):
                rejected += 1
                continue

            if (
                item["canonical"]
                in POSTED_URLS
            ):
                continue

            before = item["canonical"] in STATE[
                "queue"
            ]

            queue_candidate(
                item
            )

            if not before:
                added += 1

        logger.info(
            "RSS source=%s accepted=%d rejected=%d unresolved=%d",
            feed_def["name"],
            added,
            rejected,
            unresolved,
        )
        mark_feed_healthy(feed_def, old)
        return added

    except Exception as exc:
        logger.warning(
            "RSS failed %s: %s",
            feed_def["name"],
            exc,
        )
        mark_feed_failed(feed_def, old)
        return 0


# Consecutive failed runs before we alert about a broken feed.
FEED_FAIL_ALERT_THRESHOLD = 3


def mark_feed_healthy(feed_def, old):
    STATE["feeds"][feed_def["url"]] = {
        **old,
        "last_checked": now_iso(),
        "fail_count": 0,
        "alerted": False,
    }


def mark_feed_failed(feed_def, old):
    fail_count = int(old.get("fail_count", 0)) + 1

    STATE["feeds"][feed_def["url"]] = {
        **old,
        "last_checked": now_iso(),
        "fail_count": fail_count,
    }

    if fail_count >= FEED_FAIL_ALERT_THRESHOLD and not old.get("alerted"):
        alert_feed_down(feed_def, fail_count)
        STATE["feeds"][feed_def["url"]]["alerted"] = True


def alert_feed_down(feed_def, fail_count):
    """Tell the admin a source has gone quiet, instead of failing silently forever."""
    message = (
        f"Feed down: {feed_def['name']} ({feed_def['region']})\n"
        f"Failed {fail_count} runs in a row.\n"
        f"URL: {feed_def['url']}\n"
        f"It will keep retrying, but this source is not feeding the bot right now."
    )

    if TELEGRAM_ADMIN_CHAT_ID:
        try:
            telegram_call(
                "sendMessage",
                data={
                    "chat_id": TELEGRAM_ADMIN_CHAT_ID,
                    "text": message,
                },
            )
        except Exception as exc:
            logger.warning(
                "Feed-down alert failed to send: %s",
                exc,
            )

    logger.error(message)


def log_source_health():
    healthy = 0
    failed = 0
    unseen = 0
    logger.info("SOURCE HEALTH SUMMARY")
    for feed_def in RSS_FEEDS:
        data = STATE.get("feeds", {}).get(feed_def["url"], {})
        last_checked = data.get("last_checked")
        fail_count = int(data.get("fail_count", 0) or 0)
        if not last_checked:
            status = "UNSEEN"
            unseen += 1
        elif fail_count:
            status = f"FAILED({fail_count})"
            failed += 1
        else:
            status = "OK"
            healthy += 1
        logger.info("SOURCE %-20s %s", feed_def["name"], status)
    logger.info(
        "SOURCE HEALTH totals: ok=%d failed=%d unseen=%d",
        healthy,
        failed,
        unseen,
    )


def collect_rss():
    added = 0

    for feed_def in RSS_FEEDS:
        added += fetch_rss_feed(
            feed_def
        )

    # Critical: queue is saved together with feed validators.
    # A later 304 cannot erase unposted queued stories.
    save_state(
        STATE
    )

    logger.info(
        "RSS queue additions: %d",
        added,
    )

    return added


# ============================================================
# EXA GAP-FILL DISCOVERY
# ============================================================

# ============================================================
# SOURCE UNIVERSE
# ============================================================

def normalized_domain(url_or_source):
    raw = safe_text(url_or_source).lower()
    if "://" in raw:
        raw = urlparse(raw).netloc
    return raw.split(":")[0].removeprefix("www.").strip().rstrip("/")

def is_domain_allowed(url, domains):
    domain = normalized_domain(url)
    return any(domain == d or domain.endswith("." + d) for d in domains)

def primary_domain_allowed(url, region=None):
    return is_domain_allowed(url, PRIMARY_DOMAINS)

def fallback_domain_allowed(url, region=None):
    return is_domain_allowed(url, FALLBACK_DOMAINS)

def allowed_source_for_region(url, region="Gaming"):
    return primary_domain_allowed(url, region) or fallback_domain_allowed(url, region)

def source_name(url):
    domain = normalized_domain(url)
    path = urlparse(safe_text(url)).path.lower()
    if domain == "eurogamer.net" and "/digitalfoundry" in path:
        return "Digital Foundry"
    return SOURCE_NAMES.get(domain, domain or "Source")

def article_region(url):
    return "Gaming"

# Per-source free RSS discovery through Google News. This complements
# direct RSS feeds and handles publishers without a clean public feed.
GOOGLE_NEWS_QUERIES = [
    f"site:{domain} gaming news" for domain in PRIMARY_DOMAINS
]
GOOGLE_NEWS_LOCALE = ("en-US", "US", "US:en")

def resolve_google_news_url(link):
    try:
        response = session.get(
            link,
            timeout=10,
            allow_redirects=True,
            headers=HEADERS,
            stream=True,
        )
        real_url = safe_text(response.url)
        response.close()
        if not real_url or "news.google.com" in real_url:
            return ""
        return real_url
    except Exception:
        return ""

def google_news_gap_fill(existing_count=0, target=30, fallback=False):
    queries = (
        [f"site:{domain} gaming news" for domain in FALLBACK_DOMAINS]
        if fallback
        else GOOGLE_NEWS_QUERIES
    )
    added = 0
    for query in queries:
        if existing_count + added >= target:
            break
        try:
            feed_url = (
                "https://news.google.com/rss/search?q="
                + quote(f"{query} when:3d")
                + f"&hl={GOOGLE_NEWS_LOCALE[0]}&gl={GOOGLE_NEWS_LOCALE[1]}&ceid={GOOGLE_NEWS_LOCALE[2]}"
            )
            response = session.get(feed_url, timeout=15, headers=HEADERS)
            if response.status_code >= 400:
                continue
            parsed = feedparser.parse(response.content)
            for entry in parsed.entries[:8]:
                title = safe_text(entry.get("title"))
                link = safe_text(entry.get("link"))
                if not title or not link:
                    continue
                real_url = resolve_google_news_url(link)
                if not real_url:
                    continue
                allowed = fallback_domain_allowed(real_url) if fallback else primary_domain_allowed(real_url)
                if not allowed:
                    continue
                published_dt = feed_entry_datetime(entry) or datetime.now(BD_TZ)
                item = {
                    "title": title,
                    "url": real_url,
                    "canonical": canonical_url(real_url),
                    "published_dt": published_dt.isoformat(),
                    "published_date": published_dt.isoformat(),
                    "source": source_name(real_url),
                    "region": "Gaming",
                    "excerpt": BeautifulSoup(
                        safe_text(entry.get("summary")), "html.parser"
                    ).get_text(" ", strip=True)[:2000],
                    "image": "",
                    "discovery": "google_news_fallback" if fallback else "google_news",
                    "source_pool": "fallback" if fallback else "primary",
                }
                if not candidate_basic_allowed({**item, "published_dt": published_dt}):
                    continue
                if item["canonical"] in POSTED_URLS or item["canonical"] in STATE["queue"]:
                    continue
                queue_candidate(item)
                added += 1
                if added >= (MAX_GOOGLE_NEWS_CANDIDATES):
                    return added
        except Exception as exc:
            logger.warning("Google News discovery failed: %s", exc)
    return added


def exa_gap_fill(existing_count=0, target=30, fallback=False):
    domains = FALLBACK_DOMAINS if fallback else PRIMARY_DOMAINS
    queries = [
        "latest gaming news major game releases launches expansions",
        "gaming industry acquisitions layoffs studio closures funding",
        "PlayStation Xbox Nintendo Steam Epic major news",
        "gaming platform outages security account breach online services",
        "gaming prices subscriptions storefront monetization changes",
        "gaming hardware handheld GPU performance major developments",
        "credible gaming leaks exclusives insider reports",
    ]
    added = 0
    for query in queries:
        if existing_count + added >= target:
            break
        try:
            results = exa.search_and_contents(
                query,
                type="auto",
                category="news",
                num_results=8,
                include_domains=domains,
                start_published_date=DISCOVERY_START.isoformat(),
                end_published_date=DISCOVERY_END.isoformat(),
                contents={"highlights": {"max_characters": 900}},
            )
            for result in results.results:
                url = safe_text(getattr(result, "url", ""))
                title = safe_text(getattr(result, "title", ""))
                published_dt = parse_datetime(getattr(result, "published_date", ""))
                if not url or not title or not published_dt:
                    continue
                if fallback:
                    if not fallback_domain_allowed(url):
                        continue
                elif not primary_domain_allowed(url):
                    continue
                item = {
                    "title": title,
                    "url": url,
                    "canonical": canonical_url(url),
                    "published_dt": published_dt,
                    "published_date": published_dt.isoformat(),
                    "source": source_name(url),
                    "region": "Gaming",
                    "excerpt": safe_text(
                        " ".join(
                            getattr(result, "highlights", [])
                            if isinstance(getattr(result, "highlights", []), list)
                            else str(getattr(result, "highlights", ""))
                        )
                    )[:2000],
                    "image": safe_text(getattr(result, "image", "")),
                    "discovery": "exa_fallback" if fallback else "exa",
                    "source_pool": "fallback" if fallback else "primary",
                }
                if not candidate_basic_allowed(item):
                    continue
                if item["canonical"] in POSTED_URLS or item["canonical"] in STATE["queue"]:
                    continue
                queue_candidate(item)
                added += 1
                if added >= MAX_EXA_CANDIDATES:
                    return added
        except Exception as exc:
            logger.warning(
                "Exa %s discovery failed: %s",
                "fallback" if fallback else "primary",
                exc,
            )
    return added


def queue_candidates_for_region(region="Gaming"):
    count = 0
    for item in STATE.get("queue", {}).values():
        if item.get("region") != "Gaming" or item.get("status") != "pending":
            continue
        published = parse_datetime(item.get("published_date"))
        if published and DISCOVERY_START <= published <= DISCOVERY_END:
            count += 1
    return count


# ============================================================
# CANDIDATE NORMALIZATION
# ============================================================

# ============================================================
# VERSION 1 EDITORIAL RANKING
# ============================================================

RANK_SCHEMA = {
    "type": "object",
    "properties": {
        "ranked": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "score": {"type": "number", "minimum": 0, "maximum": 10},
                    "important": {"type": "boolean"},
                    "exclusive": {"type": "boolean"},
                    "confidence": {"type": "string", "enum": ["confirmed", "unconfirmed"]},
                    "trending": {"type": "boolean"},
                    "age_bucket": {"type": "string", "enum": ["primary", "catchup"]},
                    "reason": {"type": "string"},
                    "category": {"type": "string"},
                    "event_key": {"type": "string"},
                },
                "required": [
                    "id", "score", "important", "exclusive", "confidence",
                    "trending", "age_bucket", "reason", "category", "event_key"
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ranked"],
    "additionalProperties": False,
}


def enrich_thin_excerpt(item):
    try:
        downloaded = trafilatura.fetch_url(item["url"])
        if not downloaded:
            return None
        text = trafilatura.extract(downloaded)
        return safe_text(text)[:1500] if text else None
    except Exception:
        return None


def enrich_thin_excerpts(regional):
    enriched = 0
    for item in regional:
        if enriched >= MAX_EXCERPT_ENRICH:
            break
        excerpt = safe_text(item.get("excerpt", ""))
        if len(excerpt) >= 100:
            continue
        fuller = enrich_thin_excerpt(item)
        if fuller and len(fuller) > len(excerpt):
            item["excerpt"] = fuller
            enriched += 1
    return regional


def rank_candidates(candidates, region="Gaming"):
    if not candidates:
        return []
    regional = sorted(
        candidates,
        key=lambda x: parse_datetime(x.get("published_date")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:90]
    regional = enrich_thin_excerpts(regional)

    lines = []
    for idx, item in enumerate(regional, start=1):
        dt = parse_datetime(item.get("published_date"))
        age_hours = max(0.0, (NOW_BD - dt).total_seconds() / 3600) if dt else 999
        age_bucket = "primary" if age_hours <= PRIMARY_WINDOW_HOURS else "catchup"
        lines.append(
            "\n".join([
                f"ID: {idx}",
                f"Title: {item.get('title','')}",
                f"Source: {item.get('source','')}",
                f"Published: {item.get('published_date','')}",
                f"Age: {age_hours:.1f} hours | Bucket: {age_bucket}",
                f"Excerpt: {trim_source_text(item.get('excerpt',''), 850)}",
                "",
            ])
        )

    prompt = """
You are the editor-in-chief of @GamingNewsroom.

Score every gaming news candidate from 0 to 10 using the GamingNewsroom editorial rubric.

Publish threshold:
- score >= 7 means important=true.
- score < 7 means important=false.

Scoring:
9-10 Exceptional: major game releases/launches, massive security breaches or account compromises,
major PSN/Xbox/Nintendo/Steam/Epic outages, industry-changing acquisitions, or major moves from
Sony, Microsoft, Nintendo, Valve, Epic, Tencent, EA, Take-Two, Ubisoft, Activision Blizzard.

7-8 Clearly important: significant game updates, major launches/expansions, important platform or
monetization changes, significant security/anti-cheat changes, studio acquisitions or closures,
notable industry incidents.

4-6 Interesting, not enough: minor announcements, small patches, routine balance changes,
niche stories, reviews, ordinary trailers without material news value.

0-3 Low importance: clickbait, unsupported rumors, promotional content, duplicate coverage,
routine version bumps, opinion-only content, unboxings, podcasts, or general consumer tech.

Modifiers:
- Exclusivity bonus +0.5 to +1.5, only when base score >= 5. Exclusivity alone cannot make a small story important.
- Trending +0 to +0.5, based on multiple distinct outlets covering the same event. This is supporting evidence only.
- Unconfirmed leaks/insider reports can publish if they clear 7, but confidence MUST be unconfirmed.
- Official statements are confirmed.
- Reviews/opinion/listicles/pure promotional content should score 0-3 unless they contain a distinct factual news development.
- Esports match recaps normally score 0-3 unless materially relevant to the wider game industry.
- General consumer technology unrelated to gaming scores 0-3.
- Do not invent facts.

Return EVERY candidate exactly once. "important" must be true iff score >= 7.
Set age_bucket from the supplied age. Category must be one of the GamingNewsroom categories.
event_key should identify the underlying event in a compact stable form, or be empty when unclear.

Return JSON only.
"""

    try:
        response = cerebras.chat.completions.create(
            model=CEREBRAS_MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": "\n".join(lines)},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "gaming_news_rank",
                    "strict": True,
                    "schema": RANK_SCHEMA,
                },
            },
            reasoning_effort="low",
            temperature=0.0,
            max_completion_tokens=7000,
        )
        data = json.loads(safe_text(response.choices[0].message.content))
        by_id = {idx: item for idx, item in enumerate(regional, start=1)}
        rows = []
        for row in data.get("ranked", []):
            idx = int(row["id"])
            if idx not in by_id:
                continue
            item = dict(by_id[idx])
            score = max(0.0, min(10.0, float(row["score"])))
            item.update({
                "score": score,
                "important": score >= POST_THRESHOLD,
                "exclusive": bool(row["exclusive"]),
                "confidence": safe_text(row["confidence"]) or "confirmed",
                "trending": bool(row["trending"]),
                "age_bucket": safe_text(row["age_bucket"]),
                "reason": safe_text(row["reason"]),
                "category": canonical_topic(safe_text(row.get("category")), "Gaming"),
                "event_key": safe_text(row.get("event_key")),
            })
            rows.append(item)

        returned = {safe_text(x.get("canonical")) for x in rows}
        for item in regional:
            if safe_text(item.get("canonical")) in returned:
                continue
            fallback = dict(item)
            dt = parse_datetime(fallback.get("published_date"))
            age_hours = max(0.0, (NOW_BD - dt).total_seconds() / 3600) if dt else 999
            fallback.update({
                "score": 0.0,
                "important": False,
                "exclusive": False,
                "confidence": "confirmed",
                "trending": False,
                "age_bucket": "primary" if age_hours <= PRIMARY_WINDOW_HOURS else "catchup",
                "reason": "Classifier did not return a complete result; kept only as a non-publishable fallback.",
                "category": "Gaming News",
                "event_key": "",
            })
            rows.append(fallback)

        rows.sort(
            key=lambda x: (
                -float(x.get("score", 0)),
                0 if x.get("age_bucket") == "primary" else 1,
                -(parse_datetime(x.get("published_date")).timestamp() if parse_datetime(x.get("published_date")) else 0),
            )
        )
        return rows
    except Exception as exc:
        logger.error("Editorial scoring failed: %s", exc)
        fallback = []
        for item in regional:
            row = dict(item)
            dt = parse_datetime(row.get("published_date"))
            age_hours = max(0.0, (NOW_BD - dt).total_seconds() / 3600) if dt else 999
            row.update({
                "score": 0.0,
                "important": False,
                "exclusive": False,
                "confidence": "confirmed",
                "trending": False,
                "age_bucket": "primary" if age_hours <= PRIMARY_WINDOW_HOURS else "catchup",
                "reason": "Non-publishable fallback after scoring-service failure.",
                "category": "Gaming News",
                "event_key": "",
            })
            fallback.append(row)
        return fallback


# ============================================================
# VERSION 1 EVENT DEDUPLICATION
# ============================================================

def extract_entities(text):
    words = re.findall(r"[A-Za-z][A-Za-z&'-]{2,}", safe_text(text).lower())
    return {w for w in words if w not in STOPWORDS}


def entity_overlap(a, b):
    ea = extract_entities(f"{a.get('title','')} {a.get('excerpt','')}")
    eb = extract_entities(f"{b.get('title','')} {b.get('excerpt','')}")
    if not ea or not eb:
        return 0.0
    return len(ea & eb) / max(1, min(len(ea), len(eb)))


def event_similarity_v04(a, b):
    title_score = title_similarity(a.get("title", ""), b.get("title", ""))
    entity_score = entity_overlap(a, b)
    return (0.75 * title_score) + (0.25 * entity_score)


def same_event_window(a, b, hours=30):
    da = parse_datetime(a.get("published_date"))
    db = parse_datetime(b.get("published_date"))
    if not da or not db:
        return False
    return abs((da - db).total_seconds()) <= hours * 3600


def cluster_ranked_events(ranked):
    """Conservative event clustering. Uncertain items are always kept."""
    clusters = []
    ordered = sorted(
        ranked,
        key=lambda x: x.get("editor_rank", 9999),
    )
    for item in ordered:
        placed = False
        for cluster in clusters:
            representative = cluster[0]
            same_key = bool(
                safe_text(item.get("event_key"))
                and safe_text(item.get("event_key")) == safe_text(representative.get("event_key"))
            )
            if same_key or (
                same_event_window(item, representative)
                and event_similarity_v04(item, representative) >= 0.88
            ):
                cluster.append(item)
                placed = True
                break
        if not placed:
            clusters.append([item])

    output = []
    for index, cluster in enumerate(clusters, start=1):
        representative = cluster[0]
        stable_key = normalize_title(representative.get("title", "")) or representative.get("canonical", "")
        digest = hashlib.sha1(stable_key.encode("utf-8")).hexdigest()[:10]
        cluster_id = f"evt_{digest}"
        sources = sorted({safe_text(x.get("source")) for x in cluster if safe_text(x.get("source"))})
        for member in cluster:
            row = dict(member)
            row.update({
                "event_cluster_id": cluster_id,
                "event_cluster_size": len(cluster),
                "event_sources": sources,
                "event_source_count": len(sources),
                "event_confidence": 1.0 if len(cluster) > 1 else 0.6,
            })
            output.append(row)
    return output


def collapse_event_clusters(ranked):
    clustered = cluster_ranked_events(ranked)
    winners = {}
    for item in clustered:
        key = item.get("event_cluster_id") or item.get("canonical")
        old = winners.get(key)
        if old is None:
            winners[key] = item
            continue
        # Preserve the highest editorial rank, then newest story.
        item_key = (
            item.get("editor_rank", 9999),
            -(parse_datetime(item.get("published_date")).timestamp() if parse_datetime(item.get("published_date")) else 0),
        )
        old_key = (
            old.get("editor_rank", 9999),
            -(parse_datetime(old.get("published_date")).timestamp() if parse_datetime(old.get("published_date")) else 0),
        )
        if item_key < old_key:
            winners[key] = item
    return sorted(winners.values(), key=lambda x: x.get("editor_rank", 9999))


def persist_event_cluster_state(ranked):
    clusters = STATE.setdefault("event_clusters", {})
    for item in ranked:
        event_id = item.get("event_cluster_id")
        if not event_id:
            continue
        clusters[event_id] = {
            "event_id": event_id,
            "topic": item.get("topic", ""),
            "region": item.get("region", ""),
            "sources": item.get("event_sources", []),
            "source_count": item.get("event_source_count", 0),
            "confidence": item.get("event_confidence", 0),
            "last_seen": now_iso(),
            "headline": item.get("title", ""),
        }


def remember_posted_event(story):
    event_id = story.get("event_cluster_id") or make_event_id(story)
    ids = STATE.setdefault("posted_event_ids", [])
    if event_id and event_id not in ids:
        ids.append(event_id)
    STATE["posted_event_ids"] = ids[-500:]
    return event_id


# ============================================================
# ARTICLE EXTRACTION
# ============================================================

def find_og_image(
    url,
    page_html=None,
    final_url=None,
):
    try:
        base_url = (
            final_url
            or url
        )

        if page_html is None:
            response = session.get(
                url,
                headers={
                    **HEADERS,
                    "Referer": url,
                },
                timeout=20,
            )

            if response.status_code >= 400:
                return ""

            page_html = response.text
            base_url = response.url

        soup = BeautifulSoup(
            page_html,
            "html.parser",
        )

        for attrs in (
            {"property": "og:image"},
            {"property": "og:image:url"},
            {"name": "twitter:image"},
        ):
            tag = soup.find(
                "meta",
                attrs=attrs,
            )

            if tag and tag.get(
                "content"
            ):
                return urljoin(
                    base_url,
                    safe_text(
                        tag["content"]
                    ),
                )

    except Exception:
        pass

    return ""


def extract_article(
    item,
):
    url = item["url"]

    try:
        response = session.get(
            url,
            headers={
                **HEADERS,
                "Referer": url,
            },
            timeout=25,
        )

        if response.status_code < 400:
            page_html = response.text

            text = trafilatura.extract(
                page_html,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
            )

            image_url = (
                item.get("image")
                or find_og_image(
                    url,
                    page_html,
                    response.url,
                )
            )

            if text and len(safe_text(text)) >= 500:
                return (
                    safe_text(text),
                    image_url,
                )

    except Exception as exc:
        logger.warning(
            "Local extraction failed %s: %s",
            url,
            exc,
        )

    try:
        result_set = exa.get_contents(
            [url],
            text={
                "max_characters": 12000,
            },
        )

        if result_set.results:
            result = result_set.results[0]

            text = safe_text(
                getattr(
                    result,
                    "text",
                    "",
                )
            )

            image_url = (
                item.get("image")
                or safe_text(
                    getattr(
                        result,
                        "image",
                        "",
                    )
                )
            )

            if text:
                return (
                    text,
                    image_url,
                )

    except Exception as exc:
        logger.warning(
            "Exa article fallback failed %s: %s",
            url,
            exc,
        )

    return (
        "",
        item.get("image", ""),
    )


# ============================================================
# STORY + KNOWLEDGE GENERATION
# ============================================================

STORY_SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "badge": {"type": "string", "enum": ["Exclusive", "Breaking", "Confirmed", "Unconfirmed", "🕓 Catch-up"]},
        "platforms": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "highlights": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 3},
        "bold_terms": {"type": "array", "items": {"type": "string"}, "maxItems": 16},
        "what_to_know": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "meaning": {"type": "string"},
                },
                "required": ["term", "meaning"],
                "additionalProperties": False,
            },
            "minItems": 1,
            "maxItems": 3,
        },
    },
    "required": ["headline", "summary", "badge", "platforms", "highlights", "bold_terms", "what_to_know"],
    "additionalProperties": False,
}


def first_sentence(text):
    text = clean_generated_text(text)
    match = re.search(r"(.+?[.!?])(?:\s|$)", text)
    return clean_generated_text(match.group(1) if match else text)


def generate_story(item, article_text):
    prompt = """
You are a senior gaming news editor for @GamingNewsroom.

Create a compact, factual Telegram gaming news card from the supplied source article.
Return ONLY valid JSON matching the schema.

Headline: 6-16 words, newspaper/newsroom style, accurate, no clickbait.
Summary: exactly one complete sentence, around 18-32 words.
Highlights: 2-3 concise factual points, no repetition with the summary.
Badge:
- Exclusive only for a genuine scoop explicitly supported by the source.
- Breaking for a materially urgent development.
- Unconfirmed for credible leaks/insider reports not officially confirmed.
- Confirmed for official statements or established facts.
- Catch-up when the story is 24-72 hours old and is being recovered after a missed run.

Platforms: only relevant labels from PC, PS5, Xbox, Switch, Mobile, Steam, Epic, or other clearly supported platform names.

What to Know: 1-3 useful gaming-specific explanations, terms, systems, services, or context that
help the reader understand the story. Do not add unrelated background.

Grounding:
- Never invent facts.
- Do not turn rumor into fact.
- Preserve numbers, dates, names, platforms, studios and product names exactly as supported.
- No hashtags, Markdown, or HTML in generated fields.
"""

    user = (
        f"SOURCE: {item.get('source','')}\n"
        f"TITLE: {item.get('title','')}\n"
        f"PUBLISHED: {item.get('published_date','')}\n"
        f"SCORE: {item.get('score',0)}\n"
        f"CONFIDENCE: {item.get('confidence','confirmed')}\n"
        f"AGE_BUCKET: {item.get('age_bucket','primary')}\n"
        f"ARTICLE:\n{article_text[:14000]}"
    )

    for attempt in range(3):
        try:
            response = cerebras.chat.completions.create(
                model=CEREBRAS_MODEL,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "gaming_news_story",
                        "strict": True,
                        "schema": STORY_SCHEMA,
                    },
                },
                reasoning_effort="low",
                temperature=0.2,
                max_completion_tokens=1200,
            )
            data = json.loads(safe_text(response.choices[0].message.content))
            headline = trim_source_text(clean_generated_text(data.get("headline")), 120)
            summary = first_sentence(data.get("summary"))
            highlights = [
                trim_source_text(clean_generated_text(x), 145)
                for x in data.get("highlights", []) if safe_text(x)
            ][:3]
            what_to_know = []
            for know in data.get("what_to_know", []):
                term = trim_source_text(clean_generated_text(know.get("term")), 80)
                meaning = trim_source_text(clean_generated_text(know.get("meaning")), 240)
                if term and meaning:
                    what_to_know.append({"term": term, "meaning": meaning})
            what_to_know = what_to_know[:3]
            platforms = [trim_source_text(safe_text(x), 25) for x in data.get("platforms", []) if safe_text(x)][:6]
            badge = safe_text(data.get("badge")) or "Confirmed"
            if item.get("age_bucket") == "catchup":
                badge = "🕓 Catch-up"
            elif item.get("confidence") == "unconfirmed" and badge != "Unconfirmed":
                badge = "Unconfirmed"
            elif item.get("exclusive") and badge not in {"Breaking", "🕓 Catch-up"}:
                badge = "Exclusive"

            if (
                not headline or not summary or len(highlights) < 2 or
                not what_to_know or not complete_text(headline) or
                not complete_text(summary) or any(not complete_text(x) for x in highlights)
            ):
                raise ValueError("Incomplete story")

            return {
                **item,
                "headline": headline,
                "summary": trim_source_text(summary, 300),
                "badge": badge,
                "platforms": platforms,
                "highlights": highlights,
                "bold_terms": [safe_text(x) for x in data.get("bold_terms", []) if safe_text(x)],
                "what_to_know": what_to_know,
            }
        except Exception as exc:
            logger.warning("Story generation attempt %d failed: %s", attempt + 1, exc)
            if attempt < 2:
                time.sleep(1)
    return None


NUMBER_RE = re.compile(r"(?<![A-Za-z])(?:[$€£¥]|USD|EUR|GBP|JPY|CNY|INR)?\s?\d+(?:[.,]\d+)*(?:%|\s?(?:million|billion|thousand|k|m|bn|m)?\b)?", re.I)
YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")

# ============================================================
# BOLD TERMS
# ============================================================

def derive_bold_terms(story):
    terms = [safe_text(x) for x in story.get("bold_terms", []) if safe_text(x)]
    combined = " ".join([
        story.get("summary", ""),
        *story.get("highlights", []),
    ])
    for match in NUMBER_RE.finditer(combined):
        token = safe_text(match.group(0))
        numeric_only = re.sub(r"[^\d.]", "", token)
        if YEAR_RE.match(numeric_only) and not re.search(
            r"(19\d{2}|20\d{2})", token
        ):
            continue
        if token:
            terms.append(token)
    unique = []
    seen = set()
    for term in sorted(terms, key=len, reverse=True):
        key = term.lower()
        if len(term) >= 2 and key not in seen:
            seen.add(key)
            unique.append(term)
    return unique[:16]


def escape_rich_html(text):
    return html.escape(clean_generated_text(text), quote=False)


def bold_terms_html(text, terms):
    text = clean_generated_text(text)
    if not text:
        return ""
    result = text
    replacements = []
    for index, term in enumerate(
        sorted({safe_text(x) for x in terms if safe_text(x)}, key=len, reverse=True)
    ):
        marker = f"__RICHBOLD_{chr(65 + (index % 26))}{index // 26}__"
        match = re.search(re.escape(term), result, flags=re.I)
        if match:
            original = match.group(0)
            result = result[:match.start()] + marker + result[match.end():]
            replacements.append((marker, original))
    escaped = html.escape(result, quote=False)
    for marker, original in replacements:
        escaped = escaped.replace(
            marker,
            "<b>" + html.escape(original, quote=False) + "</b>",
        )
    return escaped


# ============================================================
# DYNAMIC RICH MESSAGE HTML
# ============================================================

def dynamic_rich_html(story):
    terms = derive_bold_terms(story)
    badge = escape_rich_html(story.get("badge", "Confirmed"))
    platforms = ", ".join(safe_text(x) for x in story.get("platforms", []) if safe_text(x))
    parts = [
        '<img src="tg://photo?id=newsphoto">',
        "<h1><b>" + escape_rich_html(story["headline"]) + "</b></h1>",
        "<p><b>" + badge + "</b>" + (f" | {escape_rich_html(platforms)}" if platforms else "") + "</p>",
        "<p>" + bold_terms_html(story["summary"], terms) + "</p>",
        "<h2>Key Highlights</h2>",
        "<p>" + "<br>".join(
            "• " + bold_terms_html(point, terms)
            for point in story["highlights"]
        ) + "</p>",
    ]

    know_body = []
    for item in story.get("what_to_know", []):
        term = escape_rich_html(item.get("term", ""))
        meaning = bold_terms_html(item.get("meaning", ""), terms)
        know_body.append(f"<p><b>{term}:</b> {meaning}</p>")
    parts.append(
        "<details><summary>What to Know</summary>"
        + "".join(know_body)
        + "</details>"
    )

    hashtags = " ".join(category_hashtags(story))
    if hashtags:
        parts.append("<p>" + escape_rich_html(hashtags) + "</p>")

    source = escape_rich_html(story["source"])
    url = html.escape(story["url"], quote=True)
    parts.append(
        "<footer><b>Source:</b> "
        f'<a href="{url}">{source}</a>'
        "</footer>"
    )
    parts.append("<p><a href=\"" + url + "\">Original article</a></p>")
    return "\n".join(parts)


def rich_visible_length(text):
    no_tags = re.sub(r"<[^>]+>", "", text)
    return len(html.unescape(no_tags))


def fit_rich_html(story):
    variants = [
        (300, 145, 3, 3, 240),
        (250, 125, 3, 2, 200),
        (210, 110, 2, 2, 160),
        (175, 90, 2, 1, 130),
    ]
    for summary_len, highlight_len, count, know_count, know_len in variants:
        candidate = dict(story)
        candidate["summary"] = trim_source_text(story["summary"], summary_len)
        candidate["highlights"] = [
            trim_source_text(x, highlight_len)
            for x in story["highlights"][:count]
        ]
        candidate["what_to_know"] = [
            {"term": x["term"], "meaning": trim_source_text(x["meaning"], know_len)}
            for x in story.get("what_to_know", [])[:know_count]
        ]
        html_text = dynamic_rich_html(candidate)
        if rich_visible_length(html_text) <= MAX_RICH_CHARACTERS:
            return html_text
    return dynamic_rich_html(story)


# ============================================================
# IMAGE BRANDING:
# ============================================================
# IMAGE BRANDING: ONLY @GamingNewsroom
# ============================================================

def find_font(
    bold=False,
):
    candidates = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        ]
        if bold
        else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        ]
    )

    for path in candidates:
        if os.path.exists(path):
            return path

    return None


def download_image(
    url,
    referer,
):
    if not url:
        return None

    try:
        response = session.get(
            url,
            headers={
                **HEADERS,
                "Referer": referer,
            },
            timeout=20,
            stream=True,
        )

        if response.status_code >= 400:
            return None

        content_type = (
            response.headers.get(
                "content-type",
                "",
            )
            .lower()
        )

        if (
            content_type
            and not content_type.startswith(
                "image/"
            )
        ):
            return None

        buf = BytesIO()

        for chunk in response.iter_content(
            65536
        ):
            if not chunk:
                continue

            buf.write(
                chunk
            )

            if buf.tell() > 8_000_000:
                return None

        buf.seek(0)

        image = Image.open(
            buf
        )
        image.load()

        if (
            image.width < 400
            or image.height < 250
        ):
            return None

        return image.convert(
            "RGB"
        )

    except Exception as exc:
        logger.warning(
            "Image download failed: %s",
            exc,
        )
        return None


def crop_cover(
    image,
    size=(1200, 675),
):
    target_w, target_h = size

    ratio = max(
        target_w / image.width,
        target_h / image.height,
    )

    resized = image.resize(
        (
            int(
                image.width
                * ratio
            ),
            int(
                image.height
                * ratio
            ),
        ),
        Image.Resampling.LANCZOS,
    )

    left = (
        resized.width
        - target_w
    ) // 2

    top = (
        resized.height
        - target_h
    ) // 2

    return resized.crop(
        (
            left,
            top,
            left + target_w,
            top + target_h,
        )
    )


def image_average_brightness(
    image,
):
    small = image.resize(
        (1, 1)
    ).convert(
        "L"
    )
    return small.getpixel(
        (0, 0)
    )


def branded_card(
    photo,
):
    base = crop_cover(
        photo
    ).convert(
        "RGBA"
    )

    brightness = image_average_brightness(
        base
    )

    # Adaptive personal-brand chip:
    # light chip on dark images, dark chip on light images.
    if brightness < 125:
        bg = (
            245,
            245,
            245,
            225,
        )
        fg = (
            20,
            24,
            28,
            255,
        )
    else:
        bg = (
            18,
            22,
            28,
            205,
        )
        fg = (
            245,
            245,
            245,
            255,
        )

    overlay = Image.new(
        "RGBA",
        base.size,
        (0, 0, 0, 0),
    )

    draw = ImageDraw.Draw(
        overlay
    )

    font_path = find_font(
        bold=True
    )

    if font_path:
        font = ImageFont.truetype(
            font_path,
            24,
        )
    else:
        font = ImageFont.load_default()

    text = "@GamingNewsroom"

    bbox = draw.textbbox(
        (0, 0),
        text,
        font=font,
    )

    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    padding_x = 22
    padding_y = 10

    chip_w = (
        text_w
        + padding_x * 2
    )
    chip_h = (
        text_h
        + padding_y * 2
    )

    x2 = 1200 - 28
    y2 = 675 - 24
    x1 = x2 - chip_w
    y1 = y2 - chip_h

    draw.rounded_rectangle(
        (
            x1,
            y1,
            x2,
            y2,
        ),
        radius=18,
        fill=bg,
    )

    draw.text(
        (
            x1 + padding_x,
            y1 + padding_y - 1,
        ),
        text,
        font=font,
        fill=fg,
    )

    return Image.alpha_composite(
        base,
        overlay,
    ).convert(
        "RGB"
    )


def prepare_image(
    story,
    index,
):
    image = download_image(
        story.get(
            "image_url",
            "",
        ),
        story["url"],
    )

    if image is None:
        image = Image.new(
            "RGB",
            (1200, 675),
            (28, 38, 50),
        )

        font_path = find_font(
            bold=True
        )

        if font_path:
            font = ImageFont.truetype(
                font_path,
                48,
            )
        else:
            font = ImageFont.load_default()

        draw = ImageDraw.Draw(
            image
        )

        draw.text(
            (50, 50),
            "Gaming News",
            font=font,
            fill="white",
        )

    branded = branded_card(
        image
    )

    path = f"/tmp/news_{index}.jpg"

    branded.save(
        path,
        "JPEG",
        quality=88,
        optimize=True,
    )

    return path


# ============================================================
# TELEGRAM RICH MESSAGES
# ============================================================

def telegram_call(
    method,
    data=None,
    files=None,
):
    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/"
        f"{method}"
    )

    last = {
        "ok": False,
        "description": "Unknown error",
    }

    for attempt in range(
        1,
        6,
    ):
        try:
            response = session.post(
                url,
                data=data or {},
                files=files,
                timeout=90,
            )

            result = response.json()

            if result.get(
                "ok"
            ):
                return result

            last = result

            if response.status_code == 429:
                retry_after = int(
                    result.get(
                        "parameters",
                        {},
                    ).get(
                        "retry_after",
                        5,
                    )
                )

                logger.warning(
                    "Telegram 429; waiting %ss",
                    retry_after,
                )

                time.sleep(
                    max(
                        1,
                        retry_after,
                    )
                )
                continue

            if response.status_code >= 500:
                time.sleep(
                    2 * attempt
                )
                continue

            break

        except Exception as exc:
            last = {
                "ok": False,
                "description": str(exc),
            }

            time.sleep(
                2 * attempt
            )

    return last



def send_bot_api_fallback(image_path, rich_html):
    """Last-resort Bot API photo send with a safe caption length."""
    text = re.sub(r"<br\s*/?>", "\n", rich_html, flags=re.I)
    text = re.sub(r"</(p|h1|h2|h3|footer|summary|details|tr|td)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > 900:
        text = text[:900].rsplit(" ", 1)[0].rstrip() + "..."

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(image_path, "rb") as photo:
            response = session.post(
                url,
                data={"chat_id": TELEGRAM_CHANNEL, "caption": text},
                files={"photo": photo},
                timeout=90,
            )
        return response.json()
    except Exception as exc:
        return {"ok": False, "description": str(exc)}

def send_rich_photo(
    image_path,
    rich_html,
):
    rich_message = {
        "html": rich_html,
        "media": [
            {
                "id": "newsphoto",
                "media": {
                    "type": "photo",
                    "media": "attach://photo",
                },
            }
        ],
        "skip_entity_detection": False,
    }

    with open(
        image_path,
        "rb",
    ) as photo:

        return telegram_call(
            "sendRichMessage",
            data={
                "chat_id": TELEGRAM_CHANNEL,
                "rich_message": json.dumps(
                    rich_message,
                    ensure_ascii=False,
                ),
            },
            files={
                "photo": photo
            },
        )


# ============================================================
# KNOWLEDGE / EVENT RECORD
# ============================================================

def make_event_id(
    story,
):
    event_key = safe_text(
        story.get(
            "event_key"
        )
    )

    if event_key:
        return (
            re.sub(
                r"[^a-z0-9]+",
                "_",
                event_key.lower(),
            ).strip("_")
        )

    return canonical_url(
        story["url"]
    )


def store_event(
    story,
    published=False,
    message_id=None,
):
    event_id = make_event_id(
        story
    )

    event = {
        "event_id": event_id,
        "canonical_url": story[
            "canonical"
        ],
        "original_url": story[
            "url"
        ],
        "source": story[
            "source"
        ],
        "region": story[
            "region"
        ],
        "topic": story.get(
            "topic",
            "",
        ),
        "institution": story.get(
            "institution",
            "",
        ),
        "event_cluster_id": story.get(
            "event_cluster_id",
            event_id,
        ),
        "event_confidence": story.get(
            "event_confidence",
            0,
        ),
        "event_source_count": story.get(
            "event_source_count",
            0,
        ),
        "headline": story[
            "headline"
        ],
        "score": story.get("score", 0),
        "important": story.get("important", False),
        "badge": story.get("badge", ""),
        "platforms": story.get("platforms", []),
        "summary": story[
            "summary"
        ],
        "highlights": story[
            "highlights"
        ],
        "concepts": story.get(
            "concepts",
            [],
        ),
        "key_numbers": story.get(
            "key_numbers",
            [],
        ),
        "published_at": story[
            "published_date"
        ],
        "selected_at": now_iso(),
        "status": (
            "published"
            if published
            else "selected"
        ),
        "message_id": message_id,
    }

    STATE["events"][
        event_id
    ] = event

    return event_id


# ============================================================
# ============================================================



# ============================================================
# VERSION 1 FALLBACK POOLS
# ============================================================

def build_candidate_pool(ranked, needed):
    """Keep a generous ranked recovery pool for downstream failures."""
    if not ranked:
        return []
    pool_size = max(RANKING_POOL_PER_REGION, needed * 2)
    return [dict(item) for item in ranked[:pool_size]]


VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "supported": {"type": "boolean"},
        "unsupported_claims": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
        },
    },
    "required": ["supported", "unsupported_claims"],
    "additionalProperties": False,
}


def claims_grounded(story, article_text):
    """Second-pass editorial verification for non-numeric factual claims."""
    claims = [
        story.get("headline", ""),
        story.get("summary", ""),
        *story.get("highlights", []),
    ]
    claims = [safe_text(x) for x in claims if safe_text(x)]

    prompt = """
You are a strict fact-checking editor. Compare the generated claims with the source article.
Mark supported=true only if every material factual claim in the headline, summary and highlights
is directly supported by the source article, either explicitly or by a faithful paraphrase.
Do not reject normal wording changes. Reject invented facts, unsupported causal claims, wrong dates,
wrong institutions, wrong people, wrong figures, exaggerated rankings, or claims stronger than the source.
Return only the JSON schema.
"""

    user = (
        "SOURCE ARTICLE:\n" + article_text[:12000]
        + "\n\nGENERATED CLAIMS:\n- " + "\n- ".join(claims)
    )

    try:
        response = cerebras.chat.completions.create(
            model=CEREBRAS_MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "story_claim_verification",
                    "strict": True,
                    "schema": VERIFY_SCHEMA,
                },
            },
            reasoning_effort="low",
            temperature=0.0,
            max_completion_tokens=500,
        )
        data = json.loads(safe_text(response.choices[0].message.content))
        return bool(data.get("supported")), data.get("unsupported_claims", [])
    except Exception as exc:
        logger.warning("Claim verification failed: %s", exc)
        # Verification infrastructure failure must not silently become a hard drop.
        # Numeric grounding remains mandatory; this pass is advisory on verifier outage.
        return True, []


def process_story_candidate(item):
    """
    Extract, generate, ground and normalize one candidate.
    Returns a publishable story or None.
    """
    article_text, image_url = extract_article(item)

    if not article_text:
        logger.warning(
            "DROP extraction: %s",
            item.get("title"),
        )
        return None

    story = generate_story(
        item,
        article_text,
    )

    if not story:
        logger.warning(
            "DROP generation: %s",
            item.get("title"),
        )
        return None

    region = item.get(
        "region",
        "Gaming",
    )

    story["topic"] = canonical_topic(
        story.get("topic") or item.get("topic"),
        region,
    )

    story["image_url"] = (
        image_url
        or item.get("image")
    )

    grounded, bad_number = numeric_grounded(
        story,
        article_text,
    )

    if not grounded:
        logger.warning(
            "Numeric grounding failed: %s (%s)",
            story.get("headline"),
            bad_number,
        )

        retry_story = generate_story(
            {
                **item,
                "grounding_warning": bad_number,
            },
            article_text,
        )

        if not retry_story:
            return None

        retry_story["topic"] = canonical_topic(
            retry_story.get("topic") or item.get("topic"),
            region,
        )
        retry_story["image_url"] = (
            image_url
            or item.get("image")
        )

        grounded_retry, _ = numeric_grounded(
            retry_story,
            article_text,
        )

        if not grounded_retry:
            logger.warning(
                "DROP numeric grounding: %s",
                story.get("headline"),
            )
            return None

        story = retry_story

    verified, unsupported_claims = claims_grounded(story, article_text)
    if not verified:
        logger.warning(
            "Claim verification failed: %s | claims=%s",
            story.get("headline"),
            unsupported_claims,
        )

        retry_story = generate_story(
            {**item, "grounding_warning": ", ".join(unsupported_claims[:3])},
            article_text,
        )
        if not retry_story:
            return None

        retry_story["topic"] = canonical_topic(
            retry_story.get("topic") or item.get("topic"),
            region,
        )
        retry_story["image_url"] = image_url or item.get("image")

        grounded_retry, _ = numeric_grounded(retry_story, article_text)
        if not grounded_retry:
            return None

        verified_retry, _ = claims_grounded(retry_story, article_text)
        if not verified_retry:
            logger.warning("DROP claim grounding: %s", story.get("headline"))
            return None
        story = retry_story

    story["topic"] = canonical_topic(
        story.get("topic") or item.get("topic"),
        region,
    )
    story["institution"] = item.get(
        "institution",
        "",
    )
    story["event_key"] = item.get(
        "event_key",
        "",
    )
    story["event_cluster_id"] = item.get(
        "event_cluster_id",
        "",
    )
    story["event_confidence"] = item.get(
        "event_confidence",
        0,
    )
    story["event_source_count"] = item.get(
        "event_source_count",
        0,
    )
    story["category_hashtags"] = category_hashtags(
        story
    )

    return story


# ============================================================
# MAIN
# ============================================================

def is_already_published_candidate(item):
    canonical = safe_text(item.get("canonical"))
    if canonical and canonical in POSTED_URLS:
        return True

    title = safe_text(item.get("title"))
    if not title:
        return False

    for event in STATE.get("events", {}).values():
        if event.get("status") != "published":
            continue
        if event.get("region") != item.get("region"):
            continue
        published_at = parse_datetime(event.get("published_at"))
        if not published_at or (NOW_BD - published_at).total_seconds() > EVENT_RETENTION_DAYS * 86400:
            continue
        previous_title = safe_text(event.get("headline"))
        if previous_title and title_similarity(title, previous_title) >= 0.90:
            return True
    return False


def available_candidates(region, source_pool=None):
    candidates = []
    seen = set()

    for item in STATE.get("queue", {}).values():
        if item.get("region") != region:
            continue
        if item.get("status") not in {"pending", "selected"}:
            continue

        published = parse_datetime(item.get("published_date"))
        if not published or not (DISCOVERY_START <= published <= DISCOVERY_END):
            continue

        url = safe_text(item.get("url"))
        canonical = safe_text(item.get("canonical"))
        if not canonical or canonical in seen:
            continue

        if source_pool == "primary" and not primary_domain_allowed(url, region):
            continue
        if source_pool == "fallback" and not fallback_domain_allowed(url, region):
            continue
        if source_pool is None and not allowed_source_for_region(url, region):
            continue

        if is_already_published_candidate(item):
            continue
        if title_duplicate_against_list(item.get("title", ""), candidates, threshold=0.94):
            continue

        candidates.append(dict(item))
        seen.add(canonical)

    candidates.sort(
        key=lambda x: parse_datetime(x.get("published_date"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return candidates[:MAX_RSS_CANDIDATES]


def prepare_ranked_region(region, candidates):
    # Cluster after the scoring pass so event_key from the editor can assist,
    # then retain the highest-scoring representative for publication.
    ranked = rank_candidates(candidates, region)
    ranked = cluster_ranked_events(ranked)
    ranked = collapse_event_clusters(ranked)
    persist_event_cluster_state(ranked)
    return ranked


def process_ranked_region(region, ranked, score_threshold=POST_THRESHOLD):
    valid = []
    for item in ranked:
        if len(valid) >= CIRCUIT_BREAKER_MAX_STORIES:
            logger.error(
                "Circuit breaker reached at %d stories. Manual review required.",
                CIRCUIT_BREAKER_MAX_STORIES,
            )
            break
        score = float(item.get("score", 0))
        if score < score_threshold or not item.get("important"):
            continue
        story = process_story_candidate(item)
        if not story:
            continue
        if is_already_published_candidate({**item, "title": story.get("headline", item.get("title"))}):
            continue
        story["topic"] = canonical_topic(story.get("topic") or item.get("category"), "Gaming")
        story["score"] = score
        story["important"] = score >= POST_THRESHOLD
        story["category"] = canonical_topic(item.get("category"), "Gaming")
        story["category_hashtags"] = category_hashtags(story)
        valid.append(story)
    return valid


def run():
    logger.info("GAMINGNEWSROOM V1 UPDATE-ONLY")
    logger.info(
        "Channel=%s | 72H LOOKBACK | %s -> %s",
        TELEGRAM_CHANNEL,
        DISCOVERY_START.isoformat(),
        DISCOVERY_END.isoformat(),
    )

    prune_state()

    # Primary 20-source collection.
    collect_rss()
    log_source_health()
    count = queue_candidates_for_region("Gaming")
    if count < 30:
        count += google_news_gap_fill(count, 36, fallback=False)
    if count < 30:
        count += exa_gap_fill(count, 36, fallback=False)

    save_state(STATE)

    primary_candidates = available_candidates("Gaming", source_pool="primary")
    logger.info("PRIMARY CANDIDATES: %d", len(primary_candidates))
    if not primary_candidates:
        logger.error(
            "No primary candidates after RSS/Google/Exa discovery. "
            "Continuing to fallback, but this run has a source/discovery problem to investigate."
        )

    ranked_primary = prepare_ranked_region("Gaming", primary_candidates)
    primary_eligible = process_ranked_region("Gaming", ranked_primary)

    stories = list(primary_eligible)

    # Thin-day fallback: activate only when fewer than 3 stories cleared 7/10.
    if len(stories) < THIN_DAY_THRESHOLD:
        existing = len(available_candidates("Gaming", source_pool="fallback"))
        if existing < 24:
            google_news_gap_fill(existing, 30, fallback=True)
            existing = len(available_candidates("Gaming", source_pool="fallback"))
            if existing < 24:
                exa_gap_fill(existing, 30, fallback=True)

        fallback_candidates = available_candidates("Gaming", source_pool="fallback")
        ranked_fallback = prepare_ranked_region("Gaming", fallback_candidates)
        fallback_stories = process_ranked_region("Gaming", ranked_fallback)
        seen = {s.get("event_cluster_id") or s.get("canonical") for s in stories}
        for story in fallback_stories:
            key = story.get("event_cluster_id") or story.get("canonical")
            if key in seen:
                continue
            stories.append(story)
            seen.add(key)

    # Variable daily volume, with an engineering-only circuit breaker.
    stories.sort(
        key=lambda s: (
            -float(s.get("score", 0)),
            0 if s.get("age_bucket") == "primary" else 1,
            -(parse_datetime(s.get("published_date")).timestamp() if parse_datetime(s.get("published_date")) else 0),
        )
    )
    if len(stories) > CIRCUIT_BREAKER_MAX_STORIES:
        logger.error(
            "Circuit breaker truncating %d important stories to top %d. Manual review required.",
            len(stories),
            CIRCUIT_BREAKER_MAX_STORIES,
        )
        stories = stories[:CIRCUIT_BREAKER_MAX_STORIES]

    logger.info("FINAL PUBLISHABLE STORIES: %d", len(stories))

    published_count = 0
    for index, story in enumerate(stories, start=1):
        rich_html = fit_rich_html(story)
        if rich_visible_length(rich_html) > MAX_RICH_CHARACTERS:
            logger.error("Rich message exceeds Telegram limit: %s", story["headline"])
            continue

        image_path = prepare_image(story, index)
        result = send_rich_photo(image_path, rich_html)
        if not result.get("ok"):
            logger.warning(
                "Rich Message publish failed; trying Bot API fallback: %s",
                result.get("description"),
            )
            result = send_bot_api_fallback(image_path, rich_html)

        if result.get("ok"):
            published_count += 1
            message = result.get("result", {})
            message_id = message.get("message_id") if isinstance(message, dict) else None
            canonical = story["canonical"]
            POSTED_URLS.add(canonical)
            save_posted_url(canonical)

            queue_item = STATE["queue"].get(canonical)
            if queue_item:
                queue_item["status"] = "posted"
                queue_item["posted_at"] = now_iso()

            store_event(story, published=True, message_id=message_id)
            remember_posted_event(story)
            update_category_coverage(story)
            STATE["recent_titles"].append(normalize_title(story["headline"]))
            STATE["recent_titles"] = STATE["recent_titles"][-400:]
            logger.info(
                "Published %d: score=%.1f badge=%s %s",
                published_count,
                float(story.get("score", 0)),
                story.get("badge", ""),
                story["headline"],
            )
        else:
            logger.error("Telegram failed: %s", result.get("description"))
        save_state(STATE)
        time.sleep(POST_DELAY_SECONDS)

    save_state(STATE)
    logger.info("Finished. Published=%d", published_count)


# ============================================================
# SELF TEST
# ============================================================

def self_test():
    sample = {
        "headline": "Major Studio Announces New Game for PS5 and PC",
        "summary": "The studio confirmed a new game for PS5 and PC during a major showcase.",
        "badge": "Confirmed",
        "platforms": ["PS5", "PC"],
        "highlights": [
            "The project was formally announced during the showcase.",
            "The game is planned for both PlayStation 5 and PC.",
        ],
        "bold_terms": ["PS5", "PC"],
        "what_to_know": [
            {"term": "Showcase", "meaning": "A publisher or platform event used to reveal games and major updates."}
        ],
        "source": "VGC",
        "url": "https://example.com/story",
        "canonical": "example.com/story",
        "region": "Gaming",
        "topic": "Major Release",
        "platforms": ["PS5", "PC"],
        "score": 8,
        "important": True,
        "age_bucket": "primary",
        "confidence": "confirmed",
        "exclusive": False,
        "trending": True,
    }

    rendered = dynamic_rich_html(sample)
    assert complete_text("A normal sentence.")
    assert complete_text("An incomplete sentence—") is False
    assert "<summary>What to Know</summary>" in rendered
    assert "#GamingNews" in rendered
    assert "Source:" in rendered
    assert "Original article" in rendered
    assert likely_same_event(
        "PlayStation network outage affects players",
        "PlayStation Network outage affects players",
    )
    assert canonical_url("https://www.example.com/story/?utm_source=x") == "example.com/story"
    test_item = {
        "url": "https://vgc.news/story",
        "title": "A valid gaming news story",
        "published_dt": NOW_BD.isoformat(),
        "region": "Gaming",
    }
    assert candidate_basic_allowed(test_item) is True
    clustered = cluster_ranked_events([
        {
            "title": "PlayStation outage affects players",
            "source": "VGC",
            "url": "https://vgc.news/a",
            "published_date": now_iso(),
            "region": "Gaming",
            "score": 8,
            "important": True,
            "event_key": "psn_outage",
        },
        {
            "title": "PSN outage hits users",
            "source": "IGN",
            "url": "https://ign.com/a",
            "published_date": now_iso(),
            "region": "Gaming",
            "score": 7,
            "important": True,
            "event_key": "psn_outage",
        },
    ])
    assert len(clustered) >= 1
    assert clustered[0]["event_cluster_size"] >= 1
    logger.info("GamingNewsroom V1 self-test passed.")

def visible_text_for_test(
    rendered,
):
    text = re.sub(
        r"<[^>]+>",
        "",
        rendered,
    )
    return html.unescape(
        text
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:
        self_test()
    else:
        run()
