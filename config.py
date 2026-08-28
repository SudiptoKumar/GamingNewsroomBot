import os
from pathlib import Path

CHANNEL = (os.getenv("TELEGRAM_CHANNEL") or "@GamingNewsroom").strip()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_API_ID = int((os.getenv("TELEGRAM_API_ID") or "0").strip() or "0")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "").strip()
EXA_API_KEY = os.getenv("EXA_API_KEY", "").strip()
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "").strip()
CEREBRAS_MODEL = (os.getenv("CEREBRAS_MODEL") or "gpt-oss-120b").strip()

STATE_FILE = Path("gaming_state.json")
POSTED_FILE = Path("posted_urls.txt")

LOOKBACK_HOURS = 72
PRIMARY_HOURS = 24
THRESHOLD = 7
THIN_DAY_THRESHOLD = 3
CIRCUIT_BREAKER = 40
MAX_SOURCE_ITEMS = 15
MAX_EXA_ITEMS = 35
POST_DELAY = 2.5
REQUEST_TIMEOUT = 18
ARTICLE_TIMEOUT = 22
MAX_ARTICLE_CHARS = 7000
TELEGRAM_CAPTION_LIMIT = 1024

HEADERS = {
    "User-Agent": "GamingNewsroom/2.0 (+https://t.me/GamingNewsroom)",
    "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.9, */*;q=0.8",
}

PRIMARY_SOURCES = [
    ("VGC", "vgc.news", 1),
    ("Insider Gaming", "insider-gaming.com", 1),
    ("Gematsu", "gematsu.com", 1),
    ("IGN", "ign.com", 1),
    ("GameSpot", "gamespot.com", 1),
    ("Eurogamer", "eurogamer.net", 1),
    ("GamesIndustry.biz", "gamesindustry.biz", 1),
    ("PC Gamer", "pcgamer.com", 1),
    ("GamesRadar+", "gamesradar.com", 2),
    ("Polygon", "polygon.com", 2),
    ("Kotaku", "kotaku.com", 2),
    ("VG247", "vg247.com", 2),
    ("Destructoid", "destructoid.com", 2),
    ("Rock Paper Shotgun", "rockpapershotgun.com", 2),
    ("Digital Foundry", "eurogamer.net", 2),
    ("Nintendo Life", "nintendolife.com", 2),
    ("Push Square", "pushsquare.com", 2),
    ("Pure Xbox", "purexbox.com", 2),
    ("Pocket Gamer", "pocketgamer.com", 2),
    ("Game Developer", "gamedeveloper.com", 2),
]

FALLBACK_SOURCES = [
    ("TheGamer", "thegamer.com"),
    ("Siliconera", "siliconera.com"),
    ("Wccftech", "wccftech.com"),
    ("TouchArcade", "toucharcade.com"),
    ("Game Rant", "gamerant.com"),
]
