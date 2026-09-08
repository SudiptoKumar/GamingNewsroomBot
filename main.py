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
from event_engine import EventEngine
from article_normalizer import hard_dedup, normalize_article
from event_store import upsert_event, ensure_v2_state, prune_event_store
from scoring import explain_score, score_breakdown
from post_validator import validate_story


# ============================================================
# CONFIGURATION
# ============================================================

EXA_API_KEY = os.environ["EXA_API_KEY"]
CEREBRAS_API_KEY = os.environ["CEREBRAS_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

TELEGRAM_CHANNEL = (os.environ.get("TELEGRAM_CHANNEL") or "@GamingNewsroom").strip()

# Optional. If set, feed-down alerts go here (a private chat/DM with the
# bot, not the public channel). If empty, alerts only go to the run log.
TELEGRAM_ADMIN_CHAT_ID = (os.environ.get("TELEGRAM_ADMIN_CHAT_ID") or "").strip()

NEWS_MODE = (os.environ.get("NEWS_MODE") or "update").strip().lower()

VALID_NEWS_MODES = {"update"}
if NEWS_MODE not in VALID_NEWS_MODES:
    raise ValueError(
        f"Invalid NEWS_MODE={NEWS_MODE!r}; expected one of {sorted(VALID_NEWS_MODES)}"
    )

CEREBRAS_MODEL = os.environ.get(
    "CEREBRAS_MODEL",
    "gpt-oss-120b",
)

POSTED_FILE = "posted_urls.txt"
STATE_FILE = "news_state.json"

BD_TZ = ZoneInfo("Asia/Dhaka")

# V3 is threshold-based, not quota-based.
PUBLISH_SCORE_THRESHOLD = int(os.environ.get("PUBLISH_SCORE_THRESHOLD", "80"))
MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS_PER_RUN", "20"))
EVENT_IDENTITY_BATCH_SIZE = int(os.environ.get("EVENT_IDENTITY_BATCH_SIZE", "35"))
RANKING_POOL_SIZE = MAX_POSTS_PER_RUN
DISCOVERY_LOOKBACK_HOURS = 24

# Reliability / quality
POST_DELAY_SECONDS = 3.5
ROLLING_DISCOVERY_HOURS = DISCOVERY_LOOKBACK_HOURS
FUTURE_TOLERANCE_MINUTES = 10
QUEUE_RETENTION_DAYS = 4
EVENT_RETENTION_DAYS = 30
MAX_RSS_CANDIDATES = 240
MAX_EXA_CANDIDATES = 60
MAX_GOOGLE_NEWS_CANDIDATES = 40
THIN_EXCERPT_CHARS = 150
MAX_EXCERPT_ENRICH = 12
MAX_SOURCE_PER_RUN = 99
MAX_RICH_CHARACTERS = 32768

# Lightweight English stopwords used only by the conservative event/entity
# deduplication layer. This is deliberately small so legitimate game entities
# and meaningful terms are not filtered out.
STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for",
    "from", "with", "by", "at", "as", "is", "are", "was", "were",
    "be", "been", "being", "has", "have", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "can",
    "this", "that", "these", "those", "it", "its", "their", "they",
    "them", "he", "she", "his", "her", "we", "our", "you", "your",
    "new", "after", "before", "over", "into", "than", "about", "from",
}

# RSS-first sources. Exa remains a fallback/gap filler.
RSS_FEEDS = [
    {"name": "IGN", "region": "Gaming", "url": "https://feeds.ign.com/ign/all"},
    {"name": "GameSpot", "region": "Gaming", "url": "https://www.gamespot.com/feeds/mashup/"},
    {"name": "VGC", "region": "Gaming", "url": "https://www.videogameschronicle.com/feed/"},
    {"name": "Eurogamer", "region": "Gaming", "url": "https://www.eurogamer.net/?format=rss"},
    {"name": "Gematsu", "region": "Gaming", "url": "https://www.gematsu.com/feed"},
    {"name": "PC Gamer", "region": "Gaming", "url": "https://www.pcgamer.com/feeds.xml"},
    {"name": "Polygon", "region": "Gaming", "url": "https://www.polygon.com/rss/index.xml"},
    {"name": "Kotaku", "region": "Gaming", "url": "https://kotaku.com/rss"},
    {"name": "GamesRadar+", "region": "Gaming", "url": "https://www.gamesradar.com/feeds.xml"},
    {"name": "Rock Paper Shotgun", "region": "Gaming", "url": "http://feeds.feedburner.com/RockPaperShotgun"},
    {"name": "Game Developer", "region": "Gaming", "url": "https://www.gamedeveloper.com/rss.xml"},
    {"name": "Insider Gaming", "region": "Gaming", "url": "https://insider-gaming.com/feed/"},
    {"name": "Nintendo Life", "region": "Gaming", "url": "https://www.nintendolife.com/feeds/latest"},
    {"name": "Push Square", "region": "Gaming", "url": "https://www.pushsquare.com/feeds/latest"},
    {"name": "Pure Xbox", "region": "Gaming", "url": "https://www.purexbox.com/feeds/latest"},
    {"name": "Shacknews", "region": "Gaming", "url": "https://www.shacknews.com/feed"},
    {"name": "Siliconera", "region": "Gaming", "url": "https://www.siliconera.com/feed/"},
    {"name": "VG247", "region": "Gaming", "url": "https://www.vg247.com/feed"},
    {"name": "TechRaptor", "region": "Gaming", "url": "https://techraptor.net/gaming/rss.xml"},
    {"name": "The Escapist", "region": "Gaming", "url": "https://www.escapistmagazine.com/v2/feed/"},
]



# ============================================================
# TAXONOMY: GAMING NEWS
# ============================================================

TOPICS = {
    "Gaming": [
        "Major Releases",
        "Game Announcements",
        "Game Updates",
        "Expansions and DLC",
        "PlayStation",
        "Xbox",
        "Nintendo",
        "PC Gaming",
        "Steam",
        "Epic Games",
        "Mobile Gaming",
        "Multiplayer",
        "Live Service",
        "Esports",
        "Game Studios",
        "Publishers",
        "Acquisitions and Mergers",
        "Studio Closures",
        "Layoffs",
        "Cancellations",
        "Game Delays",
        "Pricing and Monetization",
        "Subscriptions",
        "Storefronts",
        "Cybersecurity",
        "Account Security",
        "Anti-Cheat",
        "Gaming Hardware",
        "Platform Outages",
        "Industry Business",
        "Player Milestones",
    ]
}

INSTITUTIONS = [
    "Sony", "Microsoft", "Nintendo", "Valve", "Epic Games", "Tencent",
    "Electronic Arts", "EA", "Take-Two Interactive", "Rockstar Games",
    "Ubisoft", "Activision", "Blizzard", "Bungie", "Square Enix",
    "Capcom", "Sega", "Bandai Namco", "Embracer Group", "CD Projekt",
    "Larian Studios", "FromSoftware", "Epic Games",
]

SOURCE_NAMES = {
    "ign.com": "IGN",
    "gamespot.com": "GameSpot",
    "vgc.news": "VGC",
    "videogameschronicle.com": "VGC",
    "eurogamer.net": "Eurogamer",
    "gematsu.com": "Gematsu",
    "pcgamer.com": "PC Gamer",
    "polygon.com": "Polygon",
    "kotaku.com": "Kotaku",
    "gamesradar.com": "GamesRadar+",
    "rockpapershotgun.com": "Rock Paper Shotgun",
    "gamedeveloper.com": "Game Developer",
    "insider-gaming.com": "Insider Gaming",
    "nintendolife.com": "Nintendo Life",
    "pushsquare.com": "Push Square",
    "purexbox.com": "Pure Xbox",
    "shacknews.com": "Shacknews",
    "siliconera.com": "Siliconera",
    "vg247.com": "VG247",
    "techraptor.net": "TechRaptor",
    "escapistmagazine.com": "The Escapist",
}



# ============================================================
# CATEGORY METADATA
# ============================================================

CATEGORY_HASHTAGS = {
    "Major Releases": ["#Gaming", "#NewRelease"],
    "Game Announcements": ["#Gaming", "#GameAnnouncement"],
    "Game Updates": ["#Gaming", "#GameUpdate"],
    "Expansions and DLC": ["#Gaming", "#DLC"],
    "PlayStation": ["#PlayStation", "#Gaming"],
    "Xbox": ["#Xbox", "#Gaming"],
    "Nintendo": ["#Nintendo", "#Gaming"],
    "PC Gaming": ["#PCGaming", "#Gaming"],
    "Steam": ["#Steam", "#PCGaming"],
    "Epic Games": ["#EpicGames", "#Gaming"],
    "Mobile Gaming": ["#MobileGaming", "#Gaming"],
    "Multiplayer": ["#Multiplayer", "#Gaming"],
    "Live Service": ["#LiveService", "#Gaming"],
    "Esports": ["#Esports", "#Gaming"],
    "Game Studios": ["#GameDevelopment", "#Gaming"],
    "Publishers": ["#GamePublishers", "#Gaming"],
    "Acquisitions and Mergers": ["#GameIndustry", "#Mergers"],
    "Studio Closures": ["#GameIndustry", "#StudioClosures"],
    "Layoffs": ["#GameIndustry", "#Layoffs"],
    "Cancellations": ["#Gaming", "#GameDevelopment"],
    "Game Delays": ["#Gaming", "#GameDevelopment"],
    "Pricing and Monetization": ["#Gaming", "#Monetization"],
    "Subscriptions": ["#Gaming", "#Subscriptions"],
    "Storefronts": ["#Gaming", "#GameStore"],
    "Cybersecurity": ["#Cybersecurity", "#Gaming"],
    "Account Security": ["#AccountSecurity", "#Gaming"],
    "Anti-Cheat": ["#AntiCheat", "#Gaming"],
    "Gaming Hardware": ["#GamingHardware", "#Gaming"],
    "Platform Outages": ["#Gaming", "#Outage"],
    "Industry Business": ["#GameIndustry", "#GamingBusiness"],
    "Player Milestones": ["#Gaming", "#PlayerStats"],
}

CATEGORY_GROUPS = {
    "Platforms": {"PlayStation", "Xbox", "Nintendo", "PC Gaming", "Steam", "Epic Games", "Mobile Gaming", "Platform Outages"},
    "Games": {"Major Releases", "Game Announcements", "Game Updates", "Expansions and DLC", "Multiplayer", "Live Service", "Game Delays", "Cancellations"},
    "Industry": {"Game Studios", "Publishers", "Acquisitions and Mergers", "Studio Closures", "Layoffs", "Industry Business", "Player Milestones"},
    "Safety and Monetization": {"Pricing and Monetization", "Subscriptions", "Storefronts", "Cybersecurity", "Account Security", "Anti-Cheat"},
}

TOPIC_ALIASES = {
    "dlc": "Expansions and DLC",
    "expansion": "Expansions and DLC",
    "playstation": "PlayStation",
    "ps5": "PlayStation",
    "xbox": "Xbox",
    "nintendo": "Nintendo",
    "pc": "PC Gaming",
    "steam": "Steam",
    "epic": "Epic Games",
    "esports": "Esports",
    "layoffs": "Layoffs",
    "acquisition": "Acquisitions and Mergers",
    "merger": "Acquisitions and Mergers",
    "security": "Cybersecurity",
    "anti cheat": "Anti-Cheat",
    "anti-cheat": "Anti-Cheat",
    "monetization": "Pricing and Monetization",
}

def canonical_topic(topic, region="Gaming"):
    key = safe_text(topic).lower().strip()
    if key in TOPIC_ALIASES:
        return TOPIC_ALIASES[key]
    for item in TOPICS["Gaming"]:
        if key == item.lower():
            return item
    patterns = [
        (("playstation", "ps5", "ps4", "sony"), "PlayStation"),
        (("xbox", "microsoft"), "Xbox"),
        (("nintendo", "switch"), "Nintendo"),
        (("steam",), "Steam"),
        (("epic games",), "Epic Games"),
        (("pc", "windows gaming", "gaming pc"), "PC Gaming"),
        (("mobile", "ios", "android"), "Mobile Gaming"),
        (("esport", "esports"), "Esports"),
        (("dlc", "expansion"), "Expansions and DLC"),
        (("release", "launch"), "Major Releases"),
        (("update", "patch"), "Game Updates"),
        (("multiplayer", "online game"), "Multiplayer"),
        (("live service", "live-service"), "Live Service"),
        (("acquisition", "acquires", "merge", "merger"), "Acquisitions and Mergers"),
        (("closure", "shut down", "studio closes"), "Studio Closures"),
        (("layoff", "job cuts"), "Layoffs"),
        (("cancelled", "canceled", "cancellation"), "Cancellations"),
        (("delay", "delayed"), "Game Delays"),
        (("price", "pricing", "subscription", "monetization", "battle pass"), "Pricing and Monetization"),
        (("storefront", "store", "playstation store", "xbox store"), "Storefronts"),
        (("breach", "hack", "account security", "cybersecurity"), "Cybersecurity"),
        (("cheat", "anti-cheat", "anticheat"), "Anti-Cheat"),
        (("hardware", "console", "gpu", "graphics card"), "Gaming Hardware"),
    ]
    for needles, canonical in patterns:
        if any(needle in key for needle in needles):
            return canonical
    return "Game Updates"

def category_hashtags(story):
    tags = []
    topic = safe_text(story.get("topic"))
    institution = safe_text(story.get("institution"))
    for tag in CATEGORY_HASHTAGS.get(topic, []):
        if tag not in tags:
            tags.append(tag)
    inst_map = {
        "Sony": "#PlayStation", "Microsoft": "#Xbox", "Nintendo": "#Nintendo",
        "Valve": "#Steam", "Epic Games": "#EpicGames", "Tencent": "#GamingIndustry",
        "Electronic Arts": "#EA", "EA": "#EA", "Ubisoft": "#Ubisoft",
        "Take-Two Interactive": "#TakeTwo", "Bungie": "#Bungie",
    }
    if institution in inst_map and inst_map[institution] not in tags:
        tags.append(inst_map[institution])
    if "#Gaming" not in tags:
        tags.append("#Gaming")
    return tags[:3]


def coverage_state():
    return STATE.setdefault("category_coverage", {})


def update_category_coverage(story):
    topic = safe_text(story.get("topic"))
    if topic:
        coverage_state()[topic] = now_iso()


def refresh_category_coverage():
    coverage = coverage_state()
    for event in STATE.get("events", {}).values():
        if event.get("status") != "published":
            continue
        published_at = parse_datetime(event.get("published_at"))
        if not published_at or published_at.date() != NOW_BD.date():
            continue
        topic = safe_text(event.get("topic"))
        if topic:
            coverage[topic] = event.get("selected_at", published_at.isoformat())


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

    # AI sometimes returns Markdown emphasis markers even though the
    # generation prompt asks for plain text. Rich Message rendering then
    # exposes those literal asterisks. Remove them before any HTML bolding
    # is applied.
    text = re.sub(r"\*+", "", text)

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
    domain = (
        urlparse(
            safe_text(url)
        )
        .netloc
        .lower()
        .removeprefix("www.")
    )

    return SOURCE_NAMES.get(
        domain,
        domain or "Source",
    )


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
        "version": "V3",
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
    ensure_v2_state(STATE)
    prune_event_store(STATE, days=EVENT_RETENTION_DAYS + 15)

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
        hours=ROLLING_DISCOVERY_HOURS
    )
)
DISCOVERY_END = (
    NOW_BD
    + timedelta(
        minutes=FUTURE_TOLERANCE_MINUTES
    )
)

DISCOVERY_TARGET_PER_REGION = 18


# ============================================================
# CLIENTS
# ============================================================

exa = Exa(
    api_key=EXA_API_KEY
)

cerebras = Cerebras(
    api_key=CEREBRAS_API_KEY
)


def cerebras_create(**kwargs):
    """Single AI gateway used by V3 event selection and story generation."""
    kwargs.pop("model_name", None)
    return cerebras.chat.completions.create(
        model=CEREBRAS_MODEL,
        **kwargs,
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

        added = 0

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
PRIMARY_GAMING_DOMAINS = [
    "ign.com", "gamespot.com", "vgc.news", "videogameschronicle.com",
    "eurogamer.net", "gematsu.com", "pcgamer.com", "polygon.com",
    "kotaku.com", "gamesradar.com", "rockpapershotgun.com",
    "gamedeveloper.com", "insider-gaming.com", "nintendolife.com",
    "pushsquare.com", "purexbox.com", "shacknews.com", "siliconera.com",
    "vg247.com", "techraptor.net", "escapistmagazine.com",
]
FALLBACK_GAMING_DOMAINS = []
ALL_PRIMARY_DOMAINS = PRIMARY_GAMING_DOMAINS
ALL_FALLBACK_DOMAINS = FALLBACK_GAMING_DOMAINS
ALL_ALLOWED_DOMAINS = ALL_PRIMARY_DOMAINS

def normalized_domain(url_or_source):
    raw = safe_text(url_or_source).lower()
    if "://" in raw:
        raw = urlparse(raw).netloc
    return raw.split(":")[0].removeprefix("www.").strip().rstrip("/")

def is_domain_allowed(url, domains):
    domain = normalized_domain(url)
    return any(domain == d or domain.endswith("." + d) for d in domains)

def primary_domain_allowed(url, region=None):
    return is_domain_allowed(url, ALL_PRIMARY_DOMAINS)

def fallback_domain_allowed(url, region=None):
    return is_domain_allowed(url, ALL_FALLBACK_DOMAINS)

def allowed_source_for_region(url, region=None):
    return primary_domain_allowed(url, region) or fallback_domain_allowed(url, region)

# ============================================================
# GOOGLE NEWS RSS: FREE GAP FILL
# ============================================================

GOOGLE_NEWS_QUERIES = {
    "Gaming": [
        "gaming news PlayStation Xbox Nintendo Steam",
        "major game release update expansion gaming",
        "gaming industry studio layoffs acquisitions",
        "gaming security outage anti-cheat platform",
        "esports multiplayer live service gaming",
    ],
}

GOOGLE_NEWS_LOCALE = {"Gaming": ("en-US", "US", "US:en")}


def resolve_google_news_url(link):
    """Google News RSS gives a redirect link, not the publisher URL.
    Follow it once (without downloading the full page) to get the
    real article URL. Return "" if it cannot be resolved safely."""
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


def google_news_gap_fill(
    region,
    existing_count,
    needed,
):
    # Same thin-coverage trigger as Exa, tried first because it is free.
    if existing_count >= max(
        6,
        needed * 3,
    ):
        return 0

    queries = GOOGLE_NEWS_QUERIES.get("Gaming", [])
    hl, gl, ceid = GOOGLE_NEWS_LOCALE.get("Gaming", ("en-US", "US", "US:en"))

    added = 0

    for query in queries:
        try:
            feed_url = (
                "https://news.google.com/rss/search?q="
                + quote(f"{query} when:2d")
                + f"&hl={hl}&gl={gl}&ceid={ceid}"
            )

            response = session.get(
                feed_url,
                timeout=15,
                headers=HEADERS,
            )

            if response.status_code >= 400:
                continue

            parsed = feedparser.parse(
                response.content
            )

            for entry in parsed.entries[:6]:
                title = safe_text(
                    entry.get("title")
                )
                link = safe_text(
                    entry.get("link")
                )

                if not title or not link:
                    continue

                real_url = resolve_google_news_url(
                    link
                )

                if not real_url:
                    continue

                published_dt = feed_entry_datetime(
                    entry
                )
                date_estimated = False

                if not published_dt:
                    published_dt = datetime.now(
                        BD_TZ
                    )
                    date_estimated = True

                item = {
                    "title": title,
                    "url": real_url,
                    "canonical": canonical_url(
                        real_url
                    ),
                    "published_dt": published_dt.isoformat(),
                    "published_date": published_dt.isoformat(),
                    "source": source_name(
                        real_url
                    ),
                    "region": region,
                    "excerpt": BeautifulSoup(
                        safe_text(
                            entry.get("summary")
                        ),
                        "html.parser",
                    ).get_text(
                        " ",
                        strip=True,
                    )[:2000],
                    "image": "",
                    "discovery": "google_news",
                    "date_estimated": date_estimated,
                }

                if not primary_domain_allowed(real_url, region):
                    continue

                if not candidate_basic_allowed(
                    {
                        **item,
                        "published_dt": published_dt,
                    }
                ):
                    continue

                if item["canonical"] in POSTED_URLS:
                    continue

                if item["canonical"] in STATE["queue"]:
                    continue

                queue_candidate(
                    item
                )
                added += 1

                if added >= MAX_GOOGLE_NEWS_CANDIDATES:
                    return added

        except Exception as exc:
            logger.warning(
                "Google News gap fill failed %s: %s",
                region,
                exc,
            )

    return added


def exa_gap_fill(region, existing_count, needed, fallback=False):
    if existing_count >= max(12, needed * 3):
        return 0

    domains = FALLBACK_GAMING_DOMAINS if fallback else PRIMARY_GAMING_DOMAINS
    if not domains:
        return 0
    queries = [
        "latest major gaming news PlayStation Xbox Nintendo Steam",
        "latest major game releases updates expansions gaming industry",
        "latest gaming studio layoffs acquisitions publisher news",
        "latest gaming cybersecurity outages anti-cheat multiplayer",
        "latest esports live service gaming business news",
    ]

    added = 0
    for query in queries:
        try:
            results = exa.search_and_contents(
                query, type="auto", category="news", num_results=8,
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
                    if not fallback_domain_allowed(url, region):
                        continue
                elif not primary_domain_allowed(url, region):
                    continue
                item = {
                    "title": title, "url": url, "canonical": canonical_url(url),
                    "published_dt": published_dt.isoformat(), "published_date": published_dt.isoformat(),
                    "source": source_name(url), "region": "Gaming",
                    "excerpt": safe_text(" ".join(getattr(result, "highlights", []) if isinstance(getattr(result, "highlights", []), list) else str(getattr(result, "highlights", ""))))[:2000],
                    "image": safe_text(getattr(result, "image", "")),
                    "discovery": "exa_fallback" if fallback else "exa",
                    "source_pool": "fallback" if fallback else "primary",
                }
                if not candidate_basic_allowed({**item, "published_dt": published_dt}):
                    continue
                if item["canonical"] in POSTED_URLS or item["canonical"] in STATE["queue"]:
                    continue
                queue_candidate(item)
                added += 1
                if added >= MAX_EXA_CANDIDATES:
                    return added
        except Exception as exc:
            logger.warning("Exa %s discovery failed: %s", "fallback" if fallback else "primary", exc)
    return added


def queue_candidates_for_region(
    region,
):
    count = 0

    for item in STATE[
        "queue"
    ].values():
        if (
            item.get("region")
            == region
            and item.get("status")
            == "pending"
        ):
            published = parse_datetime(
                item.get(
                    "published_date"
                )
            )

            if (
                published
                and DISCOVERY_START
                <= published
                <= DISCOVERY_END
            ):
                count += 1

    return count


# ============================================================
# CANDIDATE NORMALIZATION
# ============================================================

# ============================================================
# V3 EVENT INTELLIGENCE ENGINE
# ============================================================

EVENT_ENGINE = EventEngine(
    ai_create=cerebras_create,
    now_dt=NOW_BD,
    threshold=PUBLISH_SCORE_THRESHOLD,
    batch_size=EVENT_IDENTITY_BATCH_SIZE,
)


# V3 selection is implemented by the single prepare_ranked_region defined below.


def persist_event_cluster_state(clusters):
    state_clusters = STATE.setdefault("event_clusters", {})
    for cluster in clusters:
        event_id = cluster.get("cluster_id")
        if not event_id:
            continue
        state_clusters[event_id] = {
            "event_id": event_id,
            "event_key": cluster.get("event_key", ""),
            "event_subject": cluster.get("event_subject", ""),
            "event_type": cluster.get("event_type", ""),
            "event_frame": cluster.get("event_frame", {}),
            "modality": cluster.get("modality", "unknown"),
            "topic": cluster.get("topic", ""),
            "institution": cluster.get("institution", ""),
            "sources": cluster.get("sources", []),
            "source_count": cluster.get("source_count", 0),
            "independent_source_count": cluster.get("independent_source_count", 0),
            "claims": cluster.get("claims", []),
            "importance_score": cluster.get("importance_score", 0),
            "score_breakdown": score_breakdown(cluster),
            "last_seen": now_iso(),
        }


def remember_posted_event(story):
    event_id = story.get("event_cluster_id") or make_event_id(story)
    ids = STATE.setdefault("posted_event_ids", [])
    if event_id and event_id not in ids:
        ids.append(event_id)
    STATE["posted_event_ids"] = ids[-500:]
    return event_id


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
        "platform": {
            "type": "string",
            "enum": ["PlayStation", "Xbox", "PC Game", "Mobile Game"],
        },
        "highlights": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 5,
        },
        "why_it_matters": {"type": "string"},
        "whats_next": {"type": "string"},
        "bold_terms": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 16,
        },
    },
    "required": [
        "headline",
        "summary",
        "platform",
        "highlights",
        "why_it_matters",
        "whats_next",
        "bold_terms",
    ],
    "additionalProperties": False,
}


def first_sentence(text):
    text = clean_generated_text(
        text
    )

    # Conservative sentence extraction. Avoids common gaming
    # abbreviations and decimals splitting incorrectly.
    protected = {
        "U.S.": "US_SENTINEL",
        "U.K.": "UK_SENTINEL",
        "E.U.": "EU_SENTINEL",
        "No.": "NO_SENTINEL",
        "Inc.": "INC_SENTINEL",
        "Ltd.": "LTD_SENTINEL",
        "Dr.": "DR_SENTINEL",
        "Mr.": "MR_SENTINEL",
        "Mrs.": "MRS_SENTINEL",
        "Ms.": "MS_SENTINEL",
    }

    working = text

    for old, marker in protected.items():
        working = working.replace(
            old,
            marker,
        )

    match = re.search(
        r"(.+?[.!?])(?:\s|$)",
        working,
    )

    if match:
        sentence = match.group(1)
    else:
        sentence = working

    for old, marker in protected.items():
        sentence = sentence.replace(
            marker,
            old,
        )

    return clean_generated_text(
        sentence
    )


def generate_story(
    item,
    article_text,
):
    topic_hint = item.get(
        "topic",
        "",
    )

    prompt = f"""
You are a senior newspaper gaming editor and gaming knowledge editor for @GamingNewsroom.

Create a compact Telegram news card from the source article.

Primary topic:
{topic_hint}

Return ONLY valid JSON matching the schema.

PUBLIC CONTENT:
- Headline: 6-14 words, accurate, newspaper style.
- Summary: exactly ONE complete sentence, about 18-28 words.
- Platform: choose EXACTLY one of PlayStation, Xbox, PC Game, Mobile Game based on the primary player platform affected.
- Highlights: 3-5 short factual points, choosing the number that best fits the story.
- Why it Matters: 2-4 complete sentences of editorial context explaining the player, platform, industry, or business significance.
- What's Next: 1-2 complete sentences stating what players should watch for next.
- No repetition between sections.
- No "..." or "…".
- Never end a headline or highlight with an ellipsis.
- No hashtags in generated fields.
- No Markdown or HTML in JSON fields.

BOLD TERMS:
- Include important game titles, characters, developers, publishers, platforms, figures, prices,
  dates, player counts, policies and gaming terms appearing in the generated headline, summary or highlights.

The public post must follow this exact order:
Photo
# HEADLINE
1-sentence summary
Platform line
## KEY HIGHLIGHTS
3-5 bullets
## WHY IT MATTERS
2-4 sentences
## WHAT'S NEXT
1-2 sentences
#hashtags
**Source:** Publication
"""

    cluster_sources = ", ".join(item.get("event_sources", []) or [item.get("source", "")])
    evidence_lines = []
    for article in (item.get("cluster_articles") or [])[:8]:
        evidence_lines.append(
            f"- {article.get('source', '')} | {article.get('published_date', '')} | {article.get('title', '')}"
        )

    user = (
        f"REGION: {item['region']}\n"
        f"SELECTED EVENT: {item.get('event_subject', item.get('title', ''))}\n"
        f"EVENT TYPE: {item.get('event_type', '')}\n"
        f"IMPORTANCE SCORE: {item.get('importance_score', 0)}/100\n"
        f"CLUSTER SOURCES: {cluster_sources}\n"
        f"PRIMARY SOURCE: {item['source']}\n"
        f"PRIMARY TITLE: {item['title']}\n"
        f"DATE: {item['published_date']}\n\n"
        f"CLUSTER EVIDENCE:\n{chr(10).join(evidence_lines)}\n\n"
        f"SELECTED ARTICLE (use this as the factual source for the final post):\n{article_text[:12000]}"
    )

    for attempt in range(3):
        try:
            response = cerebras.chat.completions.create(
                model=CEREBRAS_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": prompt,
                    },
                    {
                        "role": "user",
                        "content": user,
                    },
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "gaming_news_story_v05_0",
                        "strict": True,
                        "schema": STORY_SCHEMA,
                    },
                },
                reasoning_effort="low",
                temperature=0.2,
                max_completion_tokens=1400,
            )

            data = json.loads(
                safe_text(
                    response.choices[0]
                    .message
                    .content
                )
            )

            headline = clean_generated_text(
                data.get(
                    "headline"
                )
            )

            summary = first_sentence(
                data.get(
                    "summary"
                )
            )

            platform = safe_text(data.get("platform"))
            if platform not in {"PlayStation", "Xbox", "PC Game", "Mobile Game"}:
                raise ValueError("Invalid platform label")

            highlights = [
                clean_generated_text(x)
                for x in data.get("highlights", [])
                if clean_generated_text(x)
            ]
            if not (3 <= len(highlights) <= 5):
                raise ValueError("Highlights must contain 3-5 points")

            why_it_matters = clean_generated_text(data.get("why_it_matters"))
            whats_next = clean_generated_text(data.get("whats_next"))
            why_count = len(re.findall(r"(?<=[.!?])\s+", why_it_matters)) + (1 if why_it_matters and why_it_matters[-1] in ".!?" else 0)
            next_count = len(re.findall(r"(?<=[.!?])\s+", whats_next)) + (1 if whats_next and whats_next[-1] in ".!?" else 0)
            if not why_it_matters or not whats_next or not (2 <= why_count <= 4) or not (1 <= next_count <= 2):
                raise ValueError("Invalid Why It Matters or What's Next")

            if (
                not headline
                or not summary
                or not complete_text(headline)
                or not complete_text(summary)
                or any(not complete_text(x) for x in highlights)
                or not complete_text(why_it_matters)
                or not complete_text(whats_next)
            ):
                raise ValueError("Incomplete story")

            story = {
                **item,
                "headline": trim_source_text(headline, 110),
                "summary": trim_source_text(summary, 260),
                "platform": platform,
                "highlights": [trim_source_text(x, 130) for x in highlights],
                "why_it_matters": trim_source_text(why_it_matters, 520),
                "whats_next": trim_source_text(whats_next, 260),
                "bold_terms": [safe_text(x) for x in data.get("bold_terms", []) if safe_text(x)],
            }

            return story

        except Exception as exc:
            logger.warning(
                "Story generation attempt %d failed: %s",
                attempt + 1,
                exc,
            )

            if attempt == 0:
                time.sleep(1)

    return None


# ============================================================
# NUMERIC GROUNDING
# ============================================================

NUMBER_RE = re.compile(
    r"""
    (?:
        (?:US|U\.S\.|HK|HK\$|Tk|BDT|USD|EUR|GBP|JPY|CNY|INR|৳|\$|€|£|¥)
        \s*
    )?
    \d[\d,]*(?:\.\d+)?
    \s*
    (?:
        million|billion|trillion|
        crore|lakh|bn|mn|b|m|k|%
    )?
    """,
    re.I | re.X,
)

YEAR_RE = re.compile(
    r"^(?:19|20)\d{2}$"
)


def normalize_number(
    raw,
):
    text = (
        safe_text(raw)
        .lower()
        .replace(",", "")
        .replace("৳", "tk")
        .replace("$", "usd")
    )

    return re.sub(
        r"\s+",
        "",
        text,
    )


def numeric_tokens(text):
    tokens = []

    for match in NUMBER_RE.finditer(
        safe_text(text)
    ):
        token = safe_text(
            match.group(0)
        )

        stripped = re.sub(
            r"[^\d.]",
            "",
            token,
        )

        if (
            YEAR_RE.match(
                stripped
            )
            and not any(
                x in token.lower()
                for x in (
                    "tk",
                    "usd",
                    "bdt",
                    "$",
                    "€",
                    "£",
                    "¥",
                    "%",
                    "million",
                    "billion",
                    "crore",
                    "lakh",
                )
            )
        ):
            continue

        if token:
            tokens.append(
                token
            )

    return tokens


def numeric_grounded(
    story,
    article_text,
):
    source_numbers = [
        normalize_number(x)
        for x in numeric_tokens(
            article_text
        )
    ]

    generated_text = " ".join(
        [
            story.get(
                "headline",
                "",
            ),
            story.get(
                "summary",
                "",
            ),
            *story.get(
                "highlights",
                [],
            ),
        ]
    )

    for token in numeric_tokens(
        generated_text
    ):
        normalized = normalize_number(
            token
        )

        if not normalized:
            continue

        # Require either exact normalized occurrence or a sufficiently
        # close numeric token from source.
        if normalized not in source_numbers:
            return False, token

    return True, ""


# ============================================================
# BOLD TERMS
# ============================================================

def derive_bold_terms(
    story,
):
    terms = [
        safe_text(x)
        for x in story.get(
            "bold_terms",
            [],
        )
        if safe_text(x)
    ]

    combined = " ".join(
        [
            story.get(
                "summary",
                "",
            ),
            *story.get(
                "highlights",
                [],
            ),
        ]
    )

    # Financial figures, but do not bold bare years.
    for match in NUMBER_RE.finditer(
        combined
    ):
        token = safe_text(
            match.group(0)
        )

        numeric_only = re.sub(
            r"[^\d.]",
            "",
            token,
        )

        if (
            YEAR_RE.match(
                numeric_only
            )
            and not re.search(
                r"(Tk|BDT|USD|EUR|GBP|JPY|CNY|INR|৳|\$|€|£|¥|%|million|billion|crore|lakh)",
                token,
                re.I,
            )
        ):
            continue

        if token:
            terms.append(
                token
            )

    unique = []
    seen = set()

    for term in sorted(
        terms,
        key=len,
        reverse=True,
    ):
        key = term.lower()

        if (
            len(term) >= 2
            and key not in seen
        ):
            seen.add(key)
            unique.append(term)

    return unique[:16]


def escape_rich_html(
    text,
):
    return html.escape(
        clean_generated_text(text),
        quote=False,
    )


def bold_terms_html(
    text,
    terms,
):
    text = clean_generated_text(
        text
    )

    if not text:
        return ""

    result = text

    # Use letter-only markers to avoid collisions with numeric terms.
    replacements = []

    for index, term in enumerate(
        sorted(
            {
                safe_text(x)
                for x in terms
                if safe_text(x)
            },
            key=len,
            reverse=True,
        )
    ):
        marker = (
            f"__RICHBOLD_{chr(65 + (index % 26))}"
            f"{index // 26}__"
        )

        pattern = re.compile(
            re.escape(term),
            re.I,
        )

        match = pattern.search(
            result
        )

        if match:
            original = match.group(
                0
            )
            result = (
                result[:match.start()]
                + marker
                + result[match.end():]
            )
            replacements.append(
                (
                    marker,
                    original,
                )
            )

    escaped = html.escape(
        result,
        quote=False,
    )

    for marker, original in replacements:
        escaped = escaped.replace(
            marker,
            "<b>"
            + html.escape(
                original,
                quote=False,
            )
            + "</b>",
        )

    return escaped


# ============================================================
# DYNAMIC RICH MESSAGE HTML
# ============================================================

def dynamic_rich_html(story):
    terms = derive_bold_terms(story)
    platform = safe_text(story.get("platform", "PC Game"))
    if platform not in {"PlayStation", "Xbox", "PC Game", "Mobile Game"}:
        platform = "PC Game"

    parts = [
        '<img src="tg://photo?id=newsphoto">',
        "<h1>" + escape_rich_html(story["headline"]) + "</h1>",
        "<p>" + bold_terms_html(story["summary"], terms) + "</p>",
        "<aside>" + escape_rich_html(platform) + "</aside>",
        "<h2>KEY HIGHLIGHTS</h2>",
        "<p>" + "<br>".join(
            "• " + bold_terms_html(point, terms)
            for point in story.get("highlights", [])
        ) + "</p>",
        "<h2>WHY IT MATTERS</h2>",
        "<p>" + bold_terms_html(story.get("why_it_matters", ""), terms) + "</p>",
        "<blockquote expandable><b>WHAT'S NEXT</b><br>"
        + bold_terms_html(story.get("whats_next", ""), terms)
        + "</blockquote>",
    ]

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

    return "\n".join(parts)


def rich_visible_length(text):
    """Return Telegram-visible character count for Rich HTML text.

    Telegram limits the rendered text, not the raw HTML markup, so strip
    tags and decode HTML entities before counting characters.
    """
    no_tags = re.sub(r"<[^>]+>", "", text)
    no_attrs = re.sub(
        r"\[[^\]]+\]\([^)]+\)",
        lambda m: m.group(0).split("]")[0][1:],
        no_tags,
    )
    return len(html.unescape(no_attrs))


def fit_rich_html(story):
    variants = [
        (260, 130, 520, 260),
        (220, 115, 440, 220),
        (190, 100, 380, 200),
        (160, 85, 320, 170),
    ]

    for summary_len, highlight_len, why_len, next_len in variants:
        candidate = dict(story)
        candidate["summary"] = trim_source_text(story["summary"], summary_len)
        candidate["highlights"] = [trim_source_text(x, highlight_len) for x in story.get("highlights", [])]
        candidate["why_it_matters"] = trim_source_text(story.get("why_it_matters", ""), why_len)
        candidate["whats_next"] = trim_source_text(story.get("whats_next", ""), next_len)
        html_text = dynamic_rich_html(candidate)
        if rich_visible_length(html_text) <= MAX_RICH_CHARACTERS:
            return html_text

    return dynamic_rich_html(story)



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
    event_id = upsert_event(STATE, story, published=published, message_id=message_id)
    # Keep the current queue/event metadata in a compact compatibility index.
    STATE.setdefault("event_clusters", {}).setdefault(event_id, {
        "event_id": event_id,
        "event_key": story.get("event_key", ""),
        "event_subject": story.get("event_subject", ""),
        "event_frame": story.get("event_frame", {}),
        "status": "published" if published else "selected",
    })
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
    pool_size = max(RANKING_POOL_SIZE, needed * 2)
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
    story["event_sources"] = item.get("event_sources", [])
    story["event_subject"] = item.get("event_subject", "")
    story["event_type"] = item.get("event_type", "")
    story["importance_score"] = item.get("importance_score", 0)
    story["importance_core"] = item.get("importance_core", 0)
    story["corroboration_points"] = item.get("corroboration_points", 0)
    story["source_quality_points"] = item.get("source_quality_points", 0)
    story["freshness_points"] = item.get("freshness_points", 0)
    story["previous_event_id"] = item.get("previous_event_id", "")
    story["repeat_status"] = item.get("repeat_status", "new")
    story["repeat_reason"] = item.get("repeat_reason", "")
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
        candidates.append(dict(item))
        seen.add(canonical)

    candidates.sort(
        key=lambda x: parse_datetime(x.get("published_date"))
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return candidates[:MAX_RSS_CANDIDATES]


def prepare_ranked_region(region, candidates):
    """V3: build event records, retain cluster evidence, apply history, score, and diversify."""
    global EVENT_ENGINE
    cleaned = hard_dedup([normalize_article(x) for x in candidates])
    selected_clusters, all_clusters = EVENT_ENGINE.run(
        cleaned, STATE.get("events", {}), max_posts=MAX_POSTS_PER_RUN
    )
    persist_event_cluster_state(all_clusters)
    result = []
    for cluster in selected_clusters:
        rep = dict(cluster.get("representative") or {})
        if not rep:
            continue
        rep.update({
            "event_cluster_id": cluster.get("cluster_id", ""),
            "event_cluster_size": len(cluster.get("articles", [])),
            "event_sources": cluster.get("sources", []),
            "event_source_count": cluster.get("source_count", 0),
            "independent_source_count": cluster.get("independent_source_count", 0),
            "event_confidence": min(1.0, 0.45 + 0.12 * cluster.get("independent_source_count", 0)),
            "event_key": cluster.get("event_key", ""),
            "event_subject": cluster.get("event_subject", ""),
            "event_type": cluster.get("event_type", ""),
            "modality": cluster.get("modality", "unknown"),
            "event_frame": cluster.get("event_frame", {}),
            "importance_score": cluster.get("importance_score", 0),
            "significance_score": cluster.get("significance_score", 0),
            "coverage_score": cluster.get("coverage_score", 0),
            "originality_score": cluster.get("originality_score", 0),
            "freshness_score": cluster.get("freshness_score", 0),
            "source_trust_score": cluster.get("source_trust_score", 0),
            "important": cluster.get("publishable", False),
            "rank_reason": cluster.get("rank_reason", ""),
            "score_breakdown": score_breakdown(cluster),
            "score_explanation": explain_score(cluster),
            "previous_event_id": cluster.get("previous_event_id", ""),
            "repeat_status": cluster.get("repeat_status", "new"),
            "repeat_reason": cluster.get("repeat_reason", ""),
            "new_claims": cluster.get("new_claims", []),
            "claims": cluster.get("claims", []),
            "cluster_articles": cluster.get("articles", [])[:8],
            "cluster_sources": cluster.get("sources", []),
        })
        result.append(rep)
    logger.info(
        "V3 EVENT PIPELINE: raw=%d hard_unique=%d clusters=%d eligible=%d selected=%d threshold=%d max=%d",
        len(candidates), len(cleaned), len(all_clusters),
        sum(1 for c in all_clusters if c.get("publishable")), len(result),
        PUBLISH_SCORE_THRESHOLD, MAX_POSTS_PER_RUN
    )
    for cluster in all_clusters[:15]:
        logger.info(
            "V3 EVENT #%s score=%s sig=%s cov=%s orig=%s fresh=%s trust=%s independent=%s repeat=%s subject=%s",
            cluster.get("editor_rank", "?"), cluster.get("importance_score", 0),
            cluster.get("significance_score", 0), cluster.get("coverage_score", 0),
            cluster.get("originality_score", 0), cluster.get("freshness_score", 0),
            cluster.get("source_trust_score", 0), cluster.get("independent_source_count", 0),
            cluster.get("repeat_status", "new"), cluster.get("event_subject", "")
        )
    return result


def process_ranked_region(region, ranked):
    """Generate only after event selection. No fixed post quota."""
    valid = []
    for item in ranked:
        story = process_story_candidate(item)
        if not story:
            continue
        if story.get("canonical") in POSTED_URLS:
            continue
        valid.append(story)
        logger.info(
            "ACCEPT %s score=%s subject=%s title=%s",
            region,
            item.get("importance_score", 0),
            item.get("event_subject", ""),
            story.get("headline", ""),
        )
        if len(valid) >= MAX_POSTS_PER_RUN:
            break
    logger.info("FINAL VALID: %d | threshold=%d | max=%d", len(valid), PUBLISH_SCORE_THRESHOLD, MAX_POSTS_PER_RUN)
    return valid

def run():
    logger.info("GAMINGNEWSROOM V3 EDITORIAL-INTELLIGENCE")
    logger.info("Channel=%s Mode=%s", TELEGRAM_CHANNEL, NEWS_MODE)
    logger.info("LOOKBACK=%d hours | %s -> %s", DISCOVERY_LOOKBACK_HOURS, DISCOVERY_START.isoformat(), DISCOVERY_END.isoformat())

    prune_state()
    refresh_category_coverage()
    collect_rss()

    gaming_count = queue_candidates_for_region("Gaming")
    gaming_count += google_news_gap_fill("Gaming", gaming_count, DISCOVERY_TARGET_PER_REGION)
    exa_gap_fill("Gaming", gaming_count, DISCOVERY_TARGET_PER_REGION)

    save_state(STATE)

    candidates = available_candidates("Gaming", source_pool="primary")
    logger.info("DISCOVERY CANDIDATES: GAMING=%d", len(candidates))

    ranked = prepare_ranked_region("Gaming", candidates)
    logger.info("SELECTED EVENTS: GAMING=%d", len(ranked))

    stories = process_ranked_region("Gaming", ranked)
    logger.info("FINAL: GAMING=%d | threshold=%d | safety_max=%d", len(stories), PUBLISH_SCORE_THRESHOLD, MAX_POSTS_PER_RUN)

    published_count = 0
    for index, story in enumerate(stories, start=1):
        rich_html = fit_rich_html(story)
        if rich_visible_length(rich_html) > MAX_RICH_CHARACTERS:
            logger.error("Rich message exceeds Telegram limit: %s", story["headline"])
            continue

        image_path = prepare_image(story, index)
        result = send_rich_photo(image_path, rich_html)
        if not result.get("ok"):
            logger.warning("Rich Message publish failed; trying Bot API fallback: %s", result.get("description"))
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
            logger.info("Published %d: [%s] %s", published_count, story.get("region", ""), story["headline"])
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
    """Offline V3 regression suite for event intelligence and message safety."""
    from event_engine import EventEngine

    class FakeChoice:
        def __init__(self, content):
            self.message = type("M", (), {"content": json.dumps(content)})()

    class FakeResponse:
        def __init__(self, content):
            self.choices = [FakeChoice(content)]

    calls = {"identity": 0, "score": 0, "material": 0, "slate": 0}

    def fake_ai_create(**kwargs):
        name = kwargs["response_format"]["json_schema"]["name"]
        blocks = kwargs["messages"][1]["content"].split("\n\n")
        ids = [int(re.search(r"ID: (\d+)", b).group(1)) for b in blocks if re.search(r"ID: (\d+)", b)]
        if name == "gaming_event_identity_v2":
            calls["identity"] += 1
            items = []
            for i, block in zip(ids, blocks):
                title_m = re.search(r"Title: (.+)", block)
                title = title_m.group(1) if title_m else ""
                if "Zelda" in title:
                    row = {"event_key":"zelda ocarina remake announcement","subject":"Zelda Ocarina remake","event_type":"announcement","action":"announce","status":"current","modality":"confirmed","game":"Zelda Ocarina remake","franchise":"The Legend of Zelda","institution":"Nintendo","target":"remake","platforms":["Switch 2"],"claim":"Nintendo announced the remake","topic":"Nintendo"}
                elif "GTA" in title:
                    row = {"event_key":"gta 6 release update","subject":"GTA 6 release update","event_type":"release","action":"update","status":"current","modality":"confirmed","game":"GTA 6","franchise":"Grand Theft Auto","institution":"Rockstar Games","target":"release","platforms":["PS5"],"claim":"GTA 6 release information","topic":"Major Releases"}
                else:
                    row = {"event_key":"minor patch","subject":"Minor patch","event_type":"patch","action":"patch","status":"current","modality":"confirmed","game":"Small game","franchise":"Small game","institution":"Indie","target":"patch","platforms":["PC"],"claim":"Minor patch","topic":"Game Updates"}
                items.append({"id": i, **row})
            return FakeResponse({"items":items})
        if name == "gaming_significance_v2":
            calls["score"] += 1
            return FakeResponse({"items":[{"id":i,"significance":45 if i == 1 else 8,"reason":"Major event" if i == 1 else "Routine item"} for i in ids]})
        if name == "gaming_material_change_v2":
            calls["material"] += 1
            return FakeResponse({"same_event":True,"material_change":False,"new_claims":[],"reason":"No material development"})
        if name == "gaming_editorial_slate_v3":
            calls["slate"] += 1
            # AI deliberately proposes two same-franchise stories plus an independent story.
            # The Python hard guard must keep the strongest Zelda story and the independent story.
            return FakeResponse({
                "selected_ids": [1, 2, 3],
                "decisions": [
                    {"id": 1, "decision": "select", "reason": "Highest-value Zelda story."},
                    {"id": 2, "decision": "select", "reason": "AI proposal; should be rejected by the hard diversity guard."},
                    {"id": 3, "decision": "select", "reason": "Independent high-value story."},
                ],
            })
        raise AssertionError(name)

    now = NOW_BD
    candidates = [
        {"title":"Zelda remake announced","url":"https://ign.com/zelda","canonical":"ign.com/zelda","source":"IGN","region":"Gaming","excerpt":"Nintendo announces the remake.","published_date":now.isoformat(),"first_seen_at":now.isoformat()},
        {"title":"Zelda remake screenshots revealed","url":"https://vgc.news/zelda","canonical":"vgc.news/zelda","source":"VGC","region":"Gaming","excerpt":"New screenshots of the remake.","published_date":now.isoformat(),"first_seen_at":now.isoformat()},
        {"title":"Small indie patch released","url":"https://ign.com/patch","canonical":"ign.com/patch","source":"IGN","region":"Gaming","excerpt":"A minor routine patch.","published_date":now.isoformat(),"first_seen_at":now.isoformat()},
    ]
    engine = EventEngine(fake_ai_create, now, threshold=80, batch_size=35)
    selected, all_clusters = engine.run(candidates, {}, max_posts=20)
    assert len(all_clusters) == 2, all_clusters
    zelda = next(c for c in all_clusters if c["event_subject"] == "Zelda Ocarina remake")
    assert len(zelda["articles"]) == 2
    assert zelda["importance_score"] >= 80
    assert len(selected) == 1
    assert calls["identity"] == 1 and calls["score"] == 1 and calls["slate"] == 1

    # Explicit editorial-slate regression: two same-franchise stories must not occupy the same slate
    # unless they pass the exceptional-story rule.
    frame = lambda game, franchise, topic, event_type: {
        "game": game, "franchise": franchise, "institution": "Nintendo",
        "event_type": event_type, "action": "announce", "target": game, "modality": "confirmed"
    }
    slate_candidates = [
        {"cluster_id":"z1","event_key":"zelda-concert","event_subject":"Zelda concert","event_frame":frame("Zelda concert","The Legend of Zelda","Zelda 40th","announcement"),"event_type":"announcement","topic":"Zelda 40th","modality":"confirmed","importance_score":91,"publishable":True,"representative":{},"sources":["VGC"],"articles":[{}]},
        {"cluster_id":"z2","event_key":"zelda-remake","event_subject":"Zelda remake","event_frame":frame("Zelda remake","The Legend of Zelda","Zelda 40th","reveal"),"event_type":"reveal","topic":"Zelda 40th","modality":"confirmed","importance_score":86,"publishable":True,"representative":{},"sources":["IGN"],"articles":[{}]},
        {"cluster_id":"g1","event_key":"gta6","event_subject":"GTA 6 update","event_frame":frame("GTA 6","Grand Theft Auto","GTA 6","update"),"event_type":"update","topic":"Major Releases","modality":"confirmed","importance_score":84,"publishable":True,"representative":{},"sources":["GameSpot"],"articles":[{}]},
    ]
    slate = engine.diversify(slate_candidates, max_posts=20)
    assert [c["cluster_id"] for c in slate] == ["z1", "g1"]

    previous = {
        "evt_old": {
            "event_id":"evt_old", "status":"published", "event_key":"zelda ocarina remake announcement",
            "event_subject":"Zelda Ocarina remake",
            "event_frame":{"game":"Zelda Ocarina remake","event_type":"announcement","institution":"Nintendo","action":"announce","target":"remake","modality":"confirmed"},
            "headline":"Zelda remake", "summary":"Nintendo announced it", "claims":["Nintendo announced the remake"]
        }
    }
    selected2, clusters2 = engine.run([candidates[1]], previous, max_posts=20)
    assert selected2 == []
    assert clusters2[0]["repeat_status"] == "repeat"

    sample = {
        "headline":"**Major Game Expansion**", "summary":"The **expansion** adds content.",
        "platform":"PlayStation", "highlights":["**PlayStation** gets the expansion.","Players receive a new campaign.","The update adds new systems."],
        "why_it_matters":"The **update** adds important content.", "whats_next":"Watch for **more details**.",
        "importance_score":90
    }
    ok, errors = validate_story(sample)
    assert not ok and "markdown_asterisk" in errors
    rendered = dynamic_rich_html({**sample, "bold_terms":["PlayStation","expansion"], "source":"IGN", "url":"https://example.com/story", "region":"Gaming", "topic":"Expansions and DLC", "institution":"Sony"})
    assert "*" not in rendered
    assert "WHY IT MATTERS" in rendered
    assert "WHAT'S NEXT" in rendered
    assert "<aside>PlayStation</aside>" in rendered
    assert canonical_url("https://www.example.com/story/?utm_source=x") == "example.com/story"
    logger.info("GamingNewsroom V3 self-test passed. Calls: %s", calls)


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
