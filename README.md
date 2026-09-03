# GamingNewsroom

> Intelligent rank-driven gaming news publishing for Telegram, powered by GitHub Actions, Exa, and Cerebras.

GamingNewsroom discovers, filters, clusters, scores, verifies, and publishes gaming stories based on editorial importance. There is **no fixed publishing quota**. Each run publishes only the strongest stories that meet the configured importance threshold, subject to a safety ceiling.

## Editorial Mission

The channel serves gamers and gaming enthusiasts across PlayStation, Xbox, PC, and mobile. It prioritizes developments with meaningful player, platform, game, studio, publisher, industry, security, pricing, subscription, multiplayer, esports, or hardware impact.

Routine patches, minor fixes, unsupported rumors, promotional material, duplicate coverage, opinion-only pieces, and very niche stories are normally rejected unless the underlying event is genuinely significant.

## Intelligent Publishing Model

Every discovered candidate receives an **importance score from 0 to 100**. The default publication threshold is:

```text
PUBLISH_SCORE_THRESHOLD=80
```

A score of exactly 80 qualifies.

There is no target number of posts per run. The safety maximum is:

```text
MAX_POSTS_PER_RUN=20
```

This is a hard ceiling, **not a target**.

Examples with the default settings:

```text
20 qualifying stories  -> publish up to 20
10 qualifying stories  -> publish 10
5 qualifying stories   -> publish 5
2 qualifying stories   -> publish 2
1 qualifying story     -> publish 1
0 qualifying stories   -> publish 0
25 qualifying stories  -> publish the top 20
```

The bot never publishes a weaker story simply to fill space.

## Ranking Factors

The gaming editor scores events using:

- Importance and event significance
- Relevance to the gaming audience
- Real-world player or platform impact
- Timeliness and freshness, without allowing recency alone to dominate
- Source quality and authority
- Evidence strength
- Originality and genuinely new information
- Multi-source confirmation when available
- Audience value
- Material change versus previously published coverage

Penalties apply to duplicate or repetitive reporting, trivial news, unsupported speculation, promotional material, opinion-only coverage, and niche stories with little audience value.

## Material Change Rule

When a candidate relates to a previously published event, the editor evaluates the **new development**, not the original event. Rewrites, recaps, reactions, and commentary without a substantial new fact or outcome are rejected. A materially new release change, availability change, security development, business action, player-impacting change, or other substantive development can qualify again.

## Event Deduplication

Likely copies of the same underlying event are clustered before final ranking. Multiple publications covering one event therefore do not become multiple Telegram posts.

The cluster keeps source and evidence information so multi-source confirmation can strengthen the editorial assessment. The strongest representative is preferred using source authority, freshness, and evidence quality.

## Production Architecture

The production code keeps orchestration and policy separate. The selection policy lives in `selection_engine.py` rather than being embedded in `main.py`:

```text
main.py
├── discovery
├── article/image processing
├── Telegram publishing
└── SelectionEngine
    ├── event clustering
    ├── 0-100 importance scoring
    ├── threshold filtering
    ├── representative selection
    ├── material-change context
    └── safety-ceiling selection

ai_router.py
└── Cerebras multi-key failover, cooldown, recovery, and persistent preference
```

`SELECTION_MODEL.md` documents the editorial selection policy.

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
Pre-ranking event clustering
   ↓
Material-change context from persistent state
   ↓
0-100 LLM importance scoring
   ↓
Threshold filtering (default ≥80)
   ↓
Final event-level selection + safety ceiling
   ↓
Article extraction
   ↓
Story generation
   ↓
Numeric grounding + claim verification
   ↓
Branded/source-fallback image
   ↓
Telegram Rich Message
   ↓
Persistent state
```

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

RSS is attempted first. Google News RSS and Exa provide gap-fill discovery using the allowed gaming domains.

## Telegram Output Structure

Every published story keeps the existing GamingNewsroom public format:

```text
Photo
Headline
1-sentence news summary
[platform shown as a centered quote block]
## KEY HIGHLIGHTS
• Major fact
• Major fact
• Major fact
... (3-5 dynamically)
## WHY IT MATTERS
2-4 sentences of editorial context.
[WHAT'S NEXT appears inside a collapsed-by-default block]
#hashtag #hashtag #hashtag
**Source:** [Publication]
```

Internal score, rank, selection reason, category, and event-cluster metadata are not exposed in the public post.

## Image Pipeline

The bot uses the article image when available. If the article image cannot be obtained, it tries source metadata and publisher branding before falling back to a source-name card. The fallback does not add an upper-left channel title. The `@GamingNewsroom` brand chip remains in the lower-right.

## Persistent State

The existing `news_state.json` and `posted_urls.txt` files remain the state system. The state stores feed information, queued candidates, event records, event clusters, posted event IDs, recent titles, and ranking metadata where available. Existing state is loaded compatibly; no competing state store is introduced.

## Scheduling

The included GitHub Actions workflow still runs hourly from **07:00 through 23:00 Asia/Dhaka** and supports manual execution. The schedule itself is unchanged by the ranking upgrade.

## Configuration

Defaults:

```text
PUBLISH_SCORE_THRESHOLD=80
MAX_POSTS_PER_RUN=20
RANKING_BATCH_SIZE=35
```

The first two settings control publication policy. `MAX_POSTS_PER_RUN` is a safety maximum, never a target.


## Multi-API AI Failover

The bot uses a centralized `ai_router.py` for Cerebras requests. It supports any number of configured keys from 1 through 10:

```text
CEREBRAS_API_KEY_1
CEREBRAS_API_KEY_2
...
CEREBRAS_API_KEY_10
```

The last known successful API is persisted as the preferred API in `news_state.json`. A healthy preferred API is always tried first. The router fails over only for retryable provider/API failures such as HTTP 429, 500, 502, 503, 504, timeouts, and connection failures. Authentication failures are isolated to the affected slot. Permanent request errors are not masked by blind rotation.

Temporary failures receive a cooldown. After the cooldown expires, the API becomes eligible again automatically. The router never performs startup health-check calls and never probes other keys when the preferred API succeeds.

Legacy deployments that use `CEREBRAS_API_KEY` remain compatible: that variable is treated as API slot 1 when `CEREBRAS_API_KEY_1` is absent.

See `SETUP_MULTI_API.md` for GitHub Secrets setup and failover behavior.

## Required Secrets

Core secrets:

```text
EXA_API_KEY
TELEGRAM_BOT_TOKEN
```

Cerebras supports either legacy `CEREBRAS_API_KEY` (slot 1 compatibility) or the new multi-key set `CEREBRAS_API_KEY_1` through `CEREBRAS_API_KEY_10`.

Optional:

```text
TELEGRAM_ADMIN_CHAT_ID
CEREBRAS_MODEL
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

## Project Tree

```text
GamingNewsroom/
├── .github/
│   └── workflows/
│       └── newbot.yml
├── tests/
│   ├── test_ai_router.py
│   └── test_selection_engine.py
├── main.py
├── ai_router.py
├── selection_engine.py
├── news_state.json
├── posted_urls.txt
├── requirements.txt
├── README.md
├── SELECTION_MODEL.md
└── SETUP_MULTI_API.md
```
