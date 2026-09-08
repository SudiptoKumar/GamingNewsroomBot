# GamingNewsroom Bot V2

Event-intelligence gaming news publisher for `@GamingNewsroom`.

## V2 objective

V2 treats a **news event**, not an article, as the unit of selection. It keeps the full evidence cluster, separates coverage/originality/significance, remembers previously published events, detects material updates, selects a diverse slate, and generates a Telegram post only after the event is selected.

```text
RSS / Web Sources
        ↓
Raw Articles
        ↓
Normalize + Hard Dedup
        ↓
Gaming Event Frame
        ↓
Event Clustering + Cannot-Link Rules
        ↓
Persistent Event Record
        ↓
Cross-Run Event Matching
        ↓
Material-Change Detection
        ↓
Coverage + Originality + Significance
        ↓
Importance Score 0–100
        ↓
Threshold ≥ 80
        ↓
Editorial Slate Selection
        ↓
Best Representative
        ↓
Grounded Post Generation
        ↓
Publication Validator
        ↓
Telegram
        ↓
Event Memory
```

## Scoring model

Every event gets one explainable 0–100 score:

- Significance: 0–45
- Coverage: 0–20
- Originality: 0–15
- Freshness: 0–10
- Source trust: 0–10
- A material-update bonus of up to 5 points is applied inside the 100-point cap when a previously published event has a verified material development.

Coverage uses distinct sources and a conservative effective-independent-source estimate. Raw article count is never treated as raw corroboration.

## Event identity

The event frame tracks the concrete game, institution, event type, action, target, platforms, modality, and claims. Modalities such as `confirmed`, `reported`, `rumored`, and `denied` are kept distinct. The clustering layer has cannot-link rules so a broad franchise/company match cannot swallow unrelated events.

## Cross-run memory

`news_state.json` stores the event record, claims, published versions, evidence sources, and score components. A new article about a previously published event is suppressed unless a material new development is verified.

`posted_urls.txt` remains as a URL audit/compatibility history, but it is not the primary event-memory mechanism.

## Publication safety

A selected event is the only unit handed to the post generator. Before publication the bot validates required sections, threshold, completeness, and stray Markdown characters such as `*`.

## Configuration

Required GitHub Secrets:

- `EXA_API_KEY`
- `CEREBRAS_API_KEY`
- `TELEGRAM_BOT_TOKEN`

Optional environment variables:

- `PUBLISH_SCORE_THRESHOLD` (default `80`)
- `MAX_POSTS_PER_RUN` (default `20`)
- `EVENT_IDENTITY_BATCH_SIZE` (default `35`)
- `TELEGRAM_ADMIN_CHAT_ID`

## GitHub Actions

The workflow runs hourly from 07:00 through 23:00 Asia/Dhaka and saves `news_state.json` and `posted_urls.txt` after the run.

## Testing

Run locally:

```bash
PYTHONPATH=. pytest -q tests
python -m py_compile main.py event_engine.py article_normalizer.py event_store.py scoring.py slate_selector.py post_validator.py
```

`python main.py --self-test` is also supported after production dependencies and required environment variables are available. The self-test uses fake AI responses and does not publish to Telegram.
