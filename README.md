# Gaming News Bot

A single-provider Telegram gaming news bot built around short-window discovery, event grouping, representative coverage selection, and editorial publishing.

## Core method

```text
20 configured gaming websites
        ↓
1–6 hour news window (default: 3h)
        ↓
Check every configured source
        ↓
Normalize + hard deduplicate
        ↓
Group articles by the underlying news event
        ↓
100+ articles about one development
        → 1 event group
        ↓
Choose the best representative coverage
        ↓
Cerebras editorial selection
        ↓
Avoid duplicate coverage of the same game/event/topic
        ↓
Evidence + post validation
        ↓
Telegram
```

The bot has **no fixed publishing quota**. It can publish zero to `MAX_POSTS_PER_RUN` stories. The ceiling is a safety limit, not a target.

## Selection rules

* Every configured publication is checked for the active short window.
* `NEWS_WINDOW_HOURS` is configurable from 1 to 6 hours. Default: 3 hours.
* Articles covering the same underlying development are consolidated into one event group.
* Twenty sites reporting the same GTA 6 development can therefore become one event group and produce at most one post for that event.
* Different developments involving the same game remain separate events, while the final slate avoids unnecessary same-game/topic concentration.
* The editorial decision is based on audience value, importance, freshness, originality, impact, source quality, and evidence quality rather than one hard numeric score.
* Deterministic Python rules enforce deduplication, repeat protection, diversity, grounding, and Telegram safety after the AI decision.
* Cerebras is the only AI provider. There is no multi-provider router.

## Configuration

```text
CEREBRAS_API_KEY=...
CEREBRAS_MODEL=gpt-oss-120b
EXA_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHANNEL=@GamingNewsroom
TELEGRAM_ADMIN_CHAT_ID=...        # optional
NEWS_WINDOW_HOURS=3               # 1–6
EDITORIAL_EVENT_POOL_SIZE=50      # maximum event groups considered by editor
MAX_POSTS_PER_RUN=20              # safety ceiling, not a target
```

## Source universe

IGN, GameSpot, VGC, Eurogamer, Gematsu, PC Gamer, Polygon, Kotaku, GamesRadar+, Rock Paper Shotgun, Game Developer, Insider Gaming, Nintendo Life, Push Square, Pure Xbox, Shacknews, Siliconera, VG247, TechRaptor, and The Escapist.

## Exact repository structure

The GitHub repository intentionally uses the minimal structure shown in the requested repository view. Supporting production components are consolidated into `main.py`.

```text
Gaming News Bot/
├── .github/
│   └── workflows/
│       └── newbot.yml
├── README.md
├── main.py
├── news_state.json
├── posted_urls.txt
└── requirements.txt
```

## Verification

The GitHub workflow verifies the exact repository layout before starting the bot, then runs:

```bash
python -m py_compile main.py
python main.py --self-test
python main.py
```

The self-test uses mocked AI responses and does not publish to Telegram. Production calls use the configured Cerebras, Exa, and Telegram secrets.
