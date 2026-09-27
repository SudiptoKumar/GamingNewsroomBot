# Gaming News

Simple, RSS-first gaming news publisher for `@GamingNewsroom`.

## Core design

```text
20 trusted gaming RSS sources
        ↓
24-hour new-article window
        ↓
URL + simple title deduplication
        ↓
up to 10 newest candidates
        ↓
if fewer than 6: one Exa fallback search
        ↓
article text / RSS excerpt
        ↓
one Cerebras batch request for the candidate set
        ↓
source-derived fallback for any missing AI result
        ↓
6 publishable stories maximum
        ↓
article image → source logo → source name fallback
        ↓
Telegram Rich Message
        ↓
save posted URLs
```

The trusted RSS list is the editorial gate. The bot does not use AI to decide whether a story is important enough to publish.

## Publishing target

- Maximum: **6 posts per hourly run**
- Candidate recovery pool: **up to 10 newest articles**
- Discovery window: **24 hours**
- If fewer than 6 usable RSS articles exist, the bot makes **one Exa search** restricted to the trusted gaming domains.
- If fewer than 6 articles exist after that search, the bot publishes whatever valid stories are available. It never invents stories to fill the target.

## Cerebras design

Cerebras is used only to format the selected source articles. The bot makes **at most one Cerebras request per run** and disables the SDK's automatic retries with `max_retries=0` so the bot does not enter a hidden retry loop. One batch request per hourly run is intentionally far below the documented request-per-minute limits; the Limits page in your Cerebras account is authoritative for your project.

The current Cerebras Python SDK supports configurable retries and timeouts. The bot intentionally uses one bounded request and an immediate source-derived fallback when the request fails. See the official documentation:

- https://github.com/Cerebras/cerebras-cloud-sdk-python
- https://inference-docs.cerebras.ai/support/rate-limits
- https://inference-docs.cerebras.ai/capabilities/structured-outputs

The request uses a strict JSON schema for:

```text
headline
summary
platform
highlights[3-5]
hashtags[1-3]
```

The AI request is only a formatting step; article selection remains deterministic and source-driven.

## Exa fallback

Exa runs only when the trusted RSS pool has fewer than six new candidates.

The search is restricted to the same trusted gaming domains and the last 24 hours. The bot makes at most one Exa request in a run.

- https://docs.exa.ai/reference/search
- https://exa.ai/pricing

## Telegram output

```text
Photo

HEADLINE

1-sentence news summary

> PlayStation • Xbox • PC Game • Mobile Game

KEY HIGHLIGHTS

• Major fact
• Major fact
• Major fact
• Major fact
```

Highlights are dynamic: **3 to 5**.

The message then contains contextual hashtags and:

```text
Source: Publication
```

The headline has no visible `#` prefix.

The platform is rendered as a centered pull-quote using Telegram Rich Messages. Telegram's current Bot API supports `sendRichMessage`, headings, lists, pull-quotes, and photo blocks. A standard `sendPhoto` HTML fallback is also implemented so a temporary Rich Message failure does not automatically lose the post.

Official Telegram documentation:

- https://core.telegram.org/bots/api
- https://core.telegram.org/bots/api-changelog

## Image fallback

Image order:

```text
RSS/article image
      ↓
Article OG/Twitter image
      ↓
Publisher website logo
      ↓
Publisher name card
```

The fallback card shows the source logo, or the source name in the center when no logo is available. The `@GamingNewsroom` badge is kept in the lower-right corner. No generic channel-name text is added to the fallback card.

## State

`posted_urls.txt` stores canonicalized URLs that have already been published. This prevents the same article from being posted again on a later hourly run.

`news_state.json` stores lightweight feed and run statistics only.

## Scheduling

GitHub Actions runs every hour from **07:00 through 23:00 Asia/Dhaka** and also supports manual execution.

Required GitHub Secrets:

```text
EXA_API_KEY
CEREBRAS_API_KEY
TELEGRAM_BOT_TOKEN
```

The workflow sets:

```text
TELEGRAM_CHANNEL=@GamingNewsroom
CEREBRAS_MODEL=gpt-oss-120b
```

## Local checks

```bash
python -m py_compile main.py
python main.py --self-test
python main.py --dry-run
```

The self-test covers RSS-style candidate handling, URL/title deduplication, dynamic 3-5 highlights, Rich Message structure, image-fallback integration points, and the no-AI source fallback path.
