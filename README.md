# GamingNewsroomBot

Production-oriented gaming news publisher for `@GamingNewsroom` using GitHub Actions, RSS, Google News, Exa, Cerebras, article extraction, verification, image fallback, and Telegram delivery.

## Operating model

```text
20 primary gaming publications
        ↓
24-hour rolling discovery window
        ↓
RSS + Google News + Exa gap fill
        ↓
Deterministic gaming relevance filter
        ↓
URL + event deduplication
        ↓
Event identity + significance analysis
        ↓
Evidence-backed importance ranking
        ↓
Top 10 backup events
        ↓
Story generation / deterministic fallback
        ↓
Numeric + claim checks
        ↓
Live post validation
        ↓
First 6 valid stories
        ↓
Image resolution / publisher fallback
        ↓
Telegram
```

## Publishing target

The bot targets **6 valid posts per hourly run**. It first builds a backup slate of up to **10 events**, so a failed extraction, generation, verification, or image step does not consume a publication slot.

A quiet news cycle is allowed to publish fewer than 6. The run is reported as `NO_PUBLISH` instead of being mislabeled healthy.

## Editorial score

The event score combines:

```text
Intrinsic significance      0-50
Coverage / corroboration    0-20
Originality                 0-15
Freshness                   0-10
Source trust                0-10
Material-update bonus        0-5
```

Normal publication floor: **70/100**.

Controlled soft floor: **60/100** only for strong event types with sufficient source trust and freshness when the high-score pool is insufficient.

The score is not forced upward merely to fill six slots. The hard and soft thresholds are configured on the `EventEngine` instance, so the engine does not depend on unrelated module globals.

## AI reliability

Cerebras is routed through one guarded gateway:

```text
Per-run AI budget: 24 API attempts
Transient 408/409/429/5xx and httpx transport failures: bounded retry with backoff
401/402/403: immediate circuit open
Budget exhaustion: circuit open
Circuit open: no further AI requests
```

The current Python SDK supports configuring `max_retries`, so the project disables its automatic retries and applies the bot's own bounded retry policy. This prevents hidden SDK retries from multiplying the run's request count. urlCerebras Python SDK retry documentationhttps://github.com/Cerebras/cerebras-cloud-sdk-python#handling-errors

When the AI circuit opens after event selection, the bot can use a source-derived deterministic story fallback for already selected, sufficiently important events. It does not invent unsupported game facts.

## Production validation

The same `validate_story()` function used by self-test is also executed on the live production path before Telegram delivery. It checks headline/summary presence, 3-5 highlights, incomplete text, and Markdown asterisks that would leak into the HTML message.

## Discovery resilience

The direct RSS universe contains the supplied 20 primary gaming publications. Google News and Exa provide gap filling. When primary coverage is thin, the bot performs an additional Exa pass against the same gaming domain universe rather than depending on one RSS feed being healthy.

Known dead RSS endpoints are therefore not allowed to stop the discovery pipeline.

## Source diversity

Event selection prefers high-value distinct events and prevents the final slate from concentrating on the same game/title. The downstream publisher accepts the first 6 valid stories from the 10-event backup slate.

## Telegram output

```text
Photo
Headline
1-sentence summary
Platform quote
KEY HIGHLIGHTS
3-5 bullets
WHY IT MATTERS
2-4 sentences
WHAT'S NEXT
expandable/collapsed block
hashtags
Source
```

## Image fallback

```text
Article/RSS image
        ↓
Article metadata image
        ↓
Exa image
        ↓
Publisher logo
        ↓
Publisher name fallback
```

Fallback cards never add a `Gaming News` title banner. `@GamingNewsroom` appears only as the lower-right channel brand chip.

## Required environment

```text
EXA_API_KEY=...
CEREBRAS_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHANNEL=@GamingNewsroom
NEWS_MODE=update
```

Optional tuning:

```text
PUBLISH_TARGET=6
SELECTION_POOL_SIZE=10
NEWS_WINDOW_HOURS=24
MIN_IMPORTANCE_SCORE=70
SOFT_IMPORTANCE_SCORE=60
MAX_AI_CALLS_PER_RUN=24
AI_MAX_RETRIES=2
AI_RETRY_BASE_SECONDS=3
```

## GitHub Actions

The included workflow runs hourly from **07:00 through 23:00 Asia/Dhaka** and also supports manual execution.

Execution order:

```text
compile
→ self-test
→ production run
→ save state
```

A green workflow does not automatically mean 6 posts were published. Read the final `RUN STATUS` line:

```text
HEALTHY    = target reached without AI circuit failure
DEGRADED   = partial publication or AI degradation
NO_PUBLISH = no story qualified and no AI circuit failure
```

## Local checks

```bash
python -m py_compile main.py
python main.py --self-test
```

Normal execution:

```bash
python main.py
```
