# Gaming News Bot

Production Telegram news bot for `@GamingNewsroom`, designed for hourly operation with resilient discovery, event-level deduplication, deterministic ranking, bounded AI usage, article verification, and safe publishing.

## Production flow

```text
20 primary gaming publications
        ↓
24-hour rolling discovery window
        ↓
RSS + Google News + Exa gap fill
        ↓
Deterministic gaming-relevance filter
        ↓
URL + semantic/event deduplication
        ↓
Up to 70 strongest raw candidates
        ↓
One batched AI event-analysis stage
        ↓
Deterministic importance scoring + diversity
        ↓
Top 10 backup events
        ↓
Article extraction
        ↓
Story generation
        ↓
Numeric grounding
        ↓
Limited claim verification
        ↓
First 6 valid stories
        ↓
Article image → publisher logo → source-name fallback
        ↓
Telegram
```

## Publishing target

Each hourly run targets **6 valid gaming stories**.

The bot selects up to **10 backup events** before story generation. This means a failed extraction, generation, verification, or image step can be replaced by the next ranked event instead of reducing the whole run to zero.

The bot never fabricates a story just to reach six. When fewer than six events meet the publication rules, it publishes fewer.

## Discovery window

The active discovery window is **24 hours**. This protects the hourly scheduler from delayed RSS timestamps, feed outages, temporary source failures, and articles arriving slightly late while still preventing old stories from dominating the run.

Persistent queue and event memory prevent the same story from being published repeatedly across runs.

## Source discovery

Primary publications:

IGN, GameSpot, VGC, Eurogamer, Gematsu, PC Gamer, Polygon, Kotaku, GamesRadar+, Rock Paper Shotgun, Game Developer, Insider Gaming, Nintendo Life, Push Square, Pure Xbox, Shacknews, Siliconera, VG247, TechRaptor, and The Escapist.

Direct RSS is used where stable. Shacknews and TechRaptor remain in the source universe but are currently treated as **search-only sources** because their configured RSS endpoints are not reliable. Google News and Exa can still discover articles from those domains.

Additional official fallback domains include PlayStation Blog, Xbox Wire, Nintendo, Steam, Rockstar Games, EA, Ubisoft, Blizzard, and Bethesda.

## Relevance filtering

A deterministic prefilter removes obvious non-gaming material before expensive AI processing.

Typical exclusions include movie/TV/celebrity coverage, reviews, guides, previews, opinion pieces, generic consumer electronics, and other material without a meaningful gaming signal.

Strong gaming signals include PlayStation, Xbox, Nintendo, Switch, Steam, PC gaming, DLC, expansions, patches, multiplayer, esports, Game Pass, console/platform events, major publishers, and major game-industry developments.

## Event analysis and ranking

The bot no longer uses multiple AI editorial-selection stages.

The AI event-analysis stage identifies:

- underlying event
- game/franchise/company
- event type
- modality
- affected platform
- intrinsic significance from `0-45`
- concise editorial reason

Python then calculates the final `0-100` importance score from:

```text
Significance       45
Coverage           20
Originality        15
Freshness          10
Source trust       10
----------------------
Maximum           100
```

The publication floor is **70/100**.

The selection logic also enforces:

- one event only once
- normally one story per game in a run
- maximum 2 stories per source
- previously published event protection
- material-update detection

## AI reliability

Cerebras is still the primary AI provider, but the bot now has a run-level circuit breaker.

```text
401 / 402 / 403 / 429 / provider 5xx
        ↓
Open AI circuit
        ↓
Stop further AI calls
        ↓
Use deterministic event ranking and source-derived story fallback
```

A provider failure no longer triggers repeated generation attempts for every remaining story.

The run also has an explicit AI-call budget. The default maximum is **9 AI calls per run**.

## Story generation fallback

When Cerebras is unavailable, the bot can still create a conservative source-derived card from the extracted article:

- source headline
- source-derived summary
- 3–5 article-based highlights
- deterministic context section
- source-based next-step text

If both local article extraction and Exa article extraction fail, the bot can use a sufficiently rich RSS/search excerpt as the final evidence source instead of immediately discarding the event.

The fallback is used only for events that already passed the `70/100` importance floor and local numeric grounding.

## Claim verification

Claim verification is intentionally limited to avoid unnecessary AI pressure. The default is **1 verification call per run**.

Numeric grounding remains mandatory for generated numeric claims.

## Telegram output

```text
Photo
Headline
1-sentence summary
Platform quote block

KEY HIGHLIGHTS
• 3–5 dynamic factual highlights

WHY IT MATTERS
2–4 sentences

WHAT'S NEXT
Expandable by default

#hashtag #hashtag #hashtag
Source: Publication
```

`WHAT'S NEXT` is rendered as an expandable Telegram blockquote.

No retired publication sections are used.

## Image fallback

```text
Article/RSS image
        ↓
Article metadata image
        ↓
Exa image
        ↓
Publisher website logo
        ↓
Publisher name fallback
```

Normal article images are cropped to `1200×675` and receive only the `@GamingNewsroom` lower-right brand chip.

When the article image is unavailable, the bot attempts to discover the publisher's own logo from JSON-LD, Open Graph metadata, Apple touch icons, and favicon metadata.

When no usable logo is available, the source publication name is displayed prominently in bold in the center of the fallback card.

The fallback image does **not** add a `Gaming News` title banner.

## Scheduling

GitHub Actions runs hourly from **07:00 through 23:00 Asia/Dhaka** and also supports manual execution.

## Environment variables

```text
EXA_API_KEY=...
CEREBRAS_API_KEY=...
CEREBRAS_MODEL=gpt-oss-120b
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHANNEL=@GamingNewsroom
TELEGRAM_ADMIN_CHAT_ID=...        # optional

NEWS_WINDOW_HOURS=24
PUBLISH_TARGET=6
SELECTION_POOL_SIZE=10
MIN_IMPORTANCE_SCORE=70
EVENT_IDENTITY_BATCH_SIZE=35
MAX_EVENT_ANALYSIS_CANDIDATES=70
MAX_AI_CALLS_PER_RUN=9
CLAIM_VERIFY_LIMIT=1
```

## Repository structure

```text
GamingNewsroomBot/
├── .github/
│   └── workflows/
│       ├── newbot.yml
│       └── import-zip.yml
├── README.md
├── main.py
├── news_state.json
├── posted_urls.txt
└── requirements.txt
```

All production components remain consolidated in `main.py`.

## Verification

GitHub Actions runs:

```bash
python -m py_compile main.py
python main.py --self-test
python main.py
```

The self-test covers:

- gaming relevance filtering
- importance floor
- deterministic ranking
- 10-event backup selection
- 3–5 dynamic highlights
- AI 429 circuit breaking
- source-derived generation fallback
- image/logo/source-name fallback
- configuration invariants

The self-test does not publish to Telegram.
