# GamingNewsroomBot

Automated high-signal gaming news publisher for `@GamingNewsroom`, powered by RSS/Exa discovery, a single Cerebras AI provider, GitHub Actions, and Telegram.

## Editorial goal

Publish important gaming developments without turning one hot game or franchise into the whole edition. The bot is threshold-based, not quota-driven: normally only events scoring **80/100 or higher** can publish, subject to editorial slate selection and safety validation.

## Pipeline

```text
20 gaming sources
       ↓
RSS + Google News + Exa gap-fill
       ↓
Normalize + hard URL dedup
       ↓
AI event identity
       ↓
Event clustering / cannot-link rules
       ↓
Cross-run history + material-update check
       ↓
AI significance + deterministic evidence scoring
       ↓
Importance score 0–100
       ↓
Threshold ≥ 80
       ↓
AI editorial slate selection  ← prevents same-game/topic concentration
       ↓
Python hard diversity guard
       ↓
Best representative article
       ↓
Grounded Telegram post generation
       ↓
Post validator
       ↓
Telegram
       ↓
Persistent event memory
```

## Scoring

Each event receives:

- Significance: 0–45 (AI)
- Coverage: 0–20
- Originality: 0–15
- Freshness: 0–10
- Source trust: 0–10
- Material-update bonus: up to 5 within the 100-point cap

The score measures the event, not how many articles repeat it.

## Editorial slate selection

The strongest eligible events are presented to one final AI editor as a slate. The editor is instructed to avoid:

- duplicate events
- unnecessary multiple stories about the same game
- unnecessary multiple stories about the same franchise
- unnecessary multiple stories about the same underlying topic

Different stories from the same publisher or platform are allowed when they are genuinely independent. A second same-franchise story is allowed only when it is materially different and exceptionally important.

Python then applies a deterministic hard guard to reject unsafe AI selections. If the AI selection fails, the deterministic guard remains the fallback.

## Cross-run memory

`news_state.json` stores event frames, evidence, claims, scores, and publication history. Previously published events are suppressed unless a material new development is verified. `posted_urls.txt` remains a secondary URL audit.

## Publication safety

Generation occurs only after event selection. The validator rejects incomplete posts, below-threshold stories, Markdown leaks such as stray `*`, and other structural failures before Telegram publication.

## Configuration

Required GitHub Secrets:

```text
EXA_API_KEY
CEREBRAS_API_KEY
TELEGRAM_BOT_TOKEN
```

Optional environment variables:

```text
PUBLISH_SCORE_THRESHOLD=80
MAX_POSTS_PER_RUN=20
EVENT_IDENTITY_BATCH_SIZE=35
TELEGRAM_ADMIN_CHAT_ID=
CEREBRAS_MODEL=gpt-oss-120b
```

There is **one AI provider only: Cerebras**. No multi-provider router is used.

## GitHub Actions

The workflow runs hourly from 07:00 through 23:00 Asia/Dhaka and supports manual dispatch. State is committed only after a successful run.

## Local testing

```bash
PYTHONPATH=. pytest -q tests
python -m py_compile main.py event_engine.py article_normalizer.py event_store.py scoring.py slate_selector.py post_validator.py
```

`python main.py --self-test` is an offline regression test of event intelligence and message safety. It does not publish to Telegram.

## Repository tree

```text
GamingNewsroomBot/
├── .github/workflows/newbot.yml
├── tests/
│   ├── test_event_engine.py
│   └── test_v2_engine.py
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
