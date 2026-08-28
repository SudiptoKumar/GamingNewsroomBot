# GamingNewsroom V1

> Automated gaming news intelligence for Telegram, powered by GitHub Actions, Exa, and Cerebras.

GamingNewsroom discovers, filters, ranks, verifies, and publishes up to six high-value gaming stories per run. All eligible stories compete in one ranked gaming pool, and the bot publishes the strongest available stories rather than forcing weak category quotas.

## Editorial Mission

The channel is for gamers and gaming enthusiasts across PlayStation, Xbox, PC, and mobile. It prioritizes developments with meaningful player, platform, game, studio, publisher, industry, security, pricing, subscription, multiplayer, esports, or hardware impact.

Routine patches, minor fixes, unsupported rumors, promotional material, duplicate coverage, opinion-only pieces, and very niche stories are normally rejected unless the underlying event is genuinely significant.

## Six-Story Hourly Structure

Each scheduled run targets:

```text
6 gaming stories
```

All eligible stories compete in one ranked pool. The bot may publish fewer than six when insufficient high-quality candidates remain.

## Primary Source Universe

The primary source universe contains 20 gaming publications:

| Source | Domain |
|---|---|
| IGN | `ign.com` |
| GameSpot | `gamespot.com` |
| VGC | `vgc.news` / `videogameschronicle.com` |
| Eurogamer | `eurogamer.net` |
| Gematsu | `gematsu.com` |
| PC Gamer | `pcgamer.com` |
| Polygon | `polygon.com` |
| Kotaku | `kotaku.com` |
| GamesRadar+ | `gamesradar.com` |
| Rock Paper Shotgun | `rockpapershotgun.com` |
| Game Developer | `gamedeveloper.com` |
| Insider Gaming | `insider-gaming.com` |
| Nintendo Life | `nintendolife.com` |
| Push Square | `pushsquare.com` |
| Pure Xbox | `purexbox.com` |
| Shacknews | `shacknews.com` |
| Siliconera | `siliconera.com` |
| VG247 | `vg247.com` |
| TechRaptor | `techraptor.net` |
| The Escapist | `escapistmagazine.com` |

RSS is attempted first. Google News RSS and Exa provide gap-fill discovery using the same allowed gaming domains.

## Editorial Ranking

The ranking pass scores each candidate from 0-10 based on actual significance, not headline excitement.

```text
9-10  Exceptional industry/player impact
7-8   Clearly important gaming news
4-6   Interesting but usually not publishable
0-3   Low-value, repetitive, routine, promotional, rumor/speculation, or niche
```

A story is publishable only when its importance score is at least 7. Duplicate events are collapsed before downstream processing.

## 24-Hour Rolling Window

Every run examines a rolling 24-hour discovery window with a small future tolerance for feed timestamp skew. Persistent state, posted URLs, and event memory prevent repeated publication across hourly runs.

## Discovery Flow

```text
RSS feeds
   ↓
Google News RSS gap fill
   ↓
Exa gap fill
   ↓
Source validation
   ↓
24-hour filtering
   ↓
URL deduplication
   ↓
Event deduplication
   ↓
LLM editorial ranking
   ↓
Top gaming events
   ↓
Article extraction
   ↓
Story generation
   ↓
Numeric grounding + claim verification
   ↓
Branded image
   ↓
Telegram Rich Message
   ↓
Persistent state
```

## Telegram Output Structure

Every published story follows this order:

```text
Photo
# HEADLINE
1-sentence news summary
PlayStation | Xbox | PC Game | Mobile Game
## KEY HIGHLIGHTS
• Major fact
• Major fact
• Major fact
• Major fact
## WHY IT MATTERS
2-4 sentences of editorial context.
## WHAT'S NEXT
What players should watch for next.
#hashtag #hashtag #hashtag
**Source:** [Publication]
```

### Dynamic Key Highlights

The `KEY HIGHLIGHTS` section is dynamic. The generator may produce **3, 4, or 5 concise factual highlights**, choosing the count that best represents the story without padding or repetition.

There is no fixed four-highlight requirement.

### Platform Label

Each story receives exactly one platform label:

```text
PlayStation
Xbox
PC Game
Mobile Game
```

The label represents the primary player platform affected by the story.

### Content Rules

- Headline: 6-14 words, accurate and newspaper-style.
- Summary: exactly one complete sentence.
- Highlights: 3-5 concise factual points.
- Why It Matters: 2-4 complete sentences of editorial context.
- What's Next: 1-2 complete sentences about what players should watch.
- Hashtags: up to 3 contextual gaming hashtags.
- Source: original publication and article link.

## Image Pipeline

The bot extracts an article image where possible, resizes/crops it to the 1200×675 card format, adds the `@GamingNewsroom` brand chip, and uses a generated gaming-news fallback card when no usable source image exists.

## Verification

The bot uses two verification passes:

1. Numeric grounding checks generated numeric facts against the article.
2. Claim verification checks the generated headline, summary, and highlights against the article.

Failed verification causes regeneration or candidate rejection rather than unsupported publication.

## Scheduling

The included GitHub Actions workflow runs hourly from **07:00 through 23:00 Asia/Dhaka** and also supports manual execution.

## Required Secrets

```text
EXA_API_KEY
CEREBRAS_API_KEY
TELEGRAM_BOT_TOKEN
```

Optional:

```text
TELEGRAM_ADMIN_CHAT_ID
CEREBRAS_MODEL
```

The workflow sets:

```text
TELEGRAM_CHANNEL=@GamingNewsroom
NEWS_MODE=update
```

## Local Checks

Compile:

```bash
python -m py_compile main.py
```

Self-test:

```bash
EXA_API_KEY=dummy CEREBRAS_API_KEY=dummy TELEGRAM_BOT_TOKEN=dummy python main.py --self-test
```

Normal run:

```bash
python main.py
```
