# Gaming News Bot

A single-provider Telegram gaming news bot that checks every configured gaming publication on a short 1–6 hour window, groups coverage by the underlying news event, and selects the strongest unique stories for the audience.

## Core method

```text
20 configured gaming websites
        ↓
1–6 hour news window (default: 3h)
        ↓
Check every source
        ↓
Normalize + hard deduplicate
        ↓
Group articles by underlying event
        ↓
100+ articles about the same development
        → 1 event group
        ↓
Choose the best representative coverage
        ↓
Cerebras editorial selection
        ↓
Normally max 1 story per game/title per run
        ↓
Evidence + post validation
        ↓
Telegram
```

The bot does **not** use a fixed publishing quota. It can publish zero to `MAX_POSTS_PER_RUN` stories, depending on the news available.

## Selection rules

* Every configured source is checked each run.
* The active window is configurable from 1 to 6 hours. Default is 3 hours.
* Articles are grouped by the real underlying event, not just headline similarity.
* Twenty sites covering one GTA 6 development become one event group, not twenty posts.
* Different developments for the same game are still separate event groups, but the final slate normally publishes only **one story per game/title**.
* Different games can be published together.
* The editorial model selects audience value, importance, freshness, originality, impact, and source quality.
* Python enforces hard duplicate and safety rules after the AI decision.
* No multi-AI architecture. Cerebras is the only AI provider.

## Configurable environment

```text
CEREBRAS_API_KEY=...
CEREBRAS_MODEL=gpt-oss-120b
EXA_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHANNEL=@GamingNewsroom
TELEGRAM_ADMIN_CHAT_ID=...        # optional
NEWS_WINDOW_HOURS=3               # 1–6
EDITORIAL_EVENT_POOL_SIZE=50      # event groups sent to editor
MAX_POSTS_PER_RUN=20              # safety ceiling, not a target
```

## Source universe

IGN, GameSpot, VGC, Eurogamer, Gematsu, PC Gamer, Polygon, Kotaku, GamesRadar+, Rock Paper Shotgun, Game Developer, Insider Gaming, Nintendo Life, Push Square, Pure Xbox, Shacknews, Siliconera, VG247, TechRaptor, and The Escapist.

## Repository

```text
Gaming News Bot/
├── .github/
│   └── workflows/
│       └── newbot.yml
├── tests/
│   ├── test_event_engine.py
│   └── test_engine_regression.py
├── main.py
├── article_normalizer.py
├── event_engine.py
├── event_store.py
├── scoring.py
├── slate_selector.py
├── post_validator.py
├── news_state.json
├── posted_urls.txt
├── requirements.txt
└── README.md
```

## Testing

Run:

```bash
pytest -q tests
python -m py_compile main.py event_engine.py article_normalizer.py event_store.py scoring.py slate_selector.py post_validator.py
python main.py --self-test
```

The self-test uses mocked AI responses and does not publish to Telegram.

## Repository structure
This repository intentionally keeps the production tree minimal, matching the requested GitHub view: `.github/workflows/`, `README.md`, `main.py`, `news_state.json`, `posted_urls.txt`, and `requirements.txt`. The supporting production components are consolidated into `main.py` so no additional Python files appear in the repository root.
