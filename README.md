# GamingNewsroom V1

> Automated gaming news intelligence for Telegram, powered by GitHub Actions, Exa, and Cerebras.

GamingNewsroom discovers, filters, ranks, verifies, and publishes **up to six high-value gaming stories per run**. The bot preserves the BusinessNewsBot architecture and publication format while using a single gaming-news pool and the supplied 20-source gaming universe.

## Editorial Mission

The channel is designed for gamers and gaming enthusiasts across PC, console, and mobile. It prioritizes material developments that affect players, gaming platforms, major games, studios, publishers, subscriptions, storefronts, monetization, security, hardware, multiplayer services, esports, and the wider games industry.

The editorial system does not publish every available gaming headline. Routine patches, minor fixes, unsupported rumors, promotional material, duplicate coverage, opinion-only pieces, and very niche stories are normally rejected.

## Six-Story Hourly Structure

Each scheduled run targets:

```text
6 gaming stories
```

All eligible gaming stories compete in **one ranked pool**. There is no artificial regional quota. The bot prefers the six strongest publishable events and may publish fewer than six when insufficient high-quality candidates remain.

## Primary Source Universe

The source universe supplied for the Gaming News channel contains 20 primary publications:

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

The LLM ranking pass considers all eligible candidates from the recent window and returns an ordered list. It is instructed to prioritize genuine significance rather than sensational wording.

The Gaming News editorial score model is conceptually:

```text
9-10  Exceptional industry/player impact
7-8   Clearly important gaming news
4-6   Interesting but usually not publishable
0-3   Low-value, repetitive, routine, promotional, rumor/speculation, or niche
```

Only stories at the important end of the editorial spectrum are intended for publication. Exact duplicate events are collapsed before downstream article processing.

## 24-Hour Rolling Window

Every run examines a rolling 24-hour discovery window with a small future tolerance for feed timestamp skew. Existing state, posted URLs, and event memory prevent repeated publication across hourly runs.

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

The publication design remains the same as the BusinessNewsBot structure:

```text
Photo

Headline

One-line summary

Key Highlights

What to Know

Vocabulary

Hashtags

Source
```

The branding is changed to:

```text
@GamingNewsroom
```

Generated vocabulary is gaming-specific, and hashtags are selected from the gaming taxonomy.

## Image Pipeline

The bot extracts an article image where possible, resizes/crops it to the existing 1200×675 card format, adds the `@GamingNewsroom` brand chip, and falls back to a generated gaming-news card when no usable source image exists.

## Verification

The bot keeps the two-pass verification system:

1. Numeric grounding checks generated numeric facts against the article.
2. Claim verification checks the generated headline, summary, and highlights against the article.

If a story fails grounding, the bot retries generation. If verification still fails, that candidate is dropped rather than published with unsupported claims.

## Scheduling

The included GitHub Actions workflow runs hourly from **07:00 through 23:00 Asia/Dhaka**, matching the existing bot's scheduling pattern, and also supports manual execution.

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
## Telegram output format

Every published story follows this exact order:

1. Photo
2. `# HEADLINE`
3. One-sentence news summary
4. One platform label: `PlayStation`, `Xbox`, `PC Game`, or `Mobile Game`
5. `KEY HIGHLIGHTS` with exactly 4 bullets
6. `WHY IT MATTERS` with 2-4 sentences
7. `WHAT'S NEXT` with 1-2 sentences
8. Up to 3 contextual hashtags
9. `Source:` with the original publication link

The previous `What to Know` and `Vocabulary` sections are removed from both the generation schema and Telegram renderer.

