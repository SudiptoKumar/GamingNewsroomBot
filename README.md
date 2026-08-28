# GamingNewsroom 2.0

A fresh gaming-specific Telegram news pipeline. This is not a port of BusinessNewsBot.

## Core design

20 named gaming publications are the primary source universe. Each is queried independently through a source-scoped RSS feed, then the same domains are searched independently with Exa. The two discovery paths are merged before editorial processing.

```text
SOURCE DISCOVERY
20 gaming sources → source RSS
20 gaming domains → Exa
              ↓
72-hour normalization
              ↓
URL deduplication
              ↓
EVENT CLUSTERING
              ↓
ALREADY-PUBLISHED FILTER
              ↓
CEREBRAS IMPORTANCE SCORE 0–10
              ↓
score >= 7
              ↓
ARTICLE FETCH + IMAGE
              ↓
CEREBRAS STORY GENERATION
              ↓
TELEGRAM
              ↓
PERSIST publication/source health
```

## Editorial model

This follows the supplied GamingNewsroom README:

- 24-hour cadence
- fixed 72-hour lookback
- 0–24h primary bucket
- 24–72h catch-up window
- score 7/10 or higher publishes
- no fixed story quota
- fallback sources only when fewer than 3 stories clear the threshold
- 40-story circuit breaker is engineering-only
- unconfirmed leaks remain labeled unconfirmed
- duplicate coverage becomes one event

## Required files

Keep these state files across code deployments:

- `gaming_state.json`
- `posted_urls.txt`

## Secrets

- `EXA_API_KEY`
- `CEREBRAS_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- optional `CEREBRAS_MODEL`

## Run

```bash
pip install -r requirements.txt
python main.py --self-test
python main.py
```


## Telegram Rich Text

Publication uses Telethon's HTML message formatting so the channel matches the requested rich layout: bold headline, a code-style status/platform block, Key Highlights, an expandable `What to Know` spoiler, hashtags, and a source name with the original URL hidden behind the source hyperlink. Telethon documents `<details>` for hidden text, `<pre>` for preformatted blocks, and `<a>` for links.

Required Telegram credentials are `TELEGRAM_BOT_TOKEN`, `TELEGRAM_API_ID`, and `TELEGRAM_API_HASH`. These are still one Telegram integration, not an additional news/data API.
