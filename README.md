# GamingNewsroom V1

> Automated daily gaming news digest for Telegram (@GamingNewsroom), powered by GitHub Actions, Exa, and Cerebras.

GamingNewsroom runs once every 24 hours and publishes every gaming story that clears a **7/10 importance threshold**. There is **no fixed editorial post count**.

A quiet day can publish a few stories. A major day can publish many. An engineering-only circuit breaker stops abnormal classifier output at 40 stories and flags the run for review.

## Editorial Mission

GamingNewsroom is designed to behave like an automated gaming newsroom, not a generic feed.

It prioritizes:

- Major game releases, launches, expansions and DLC
- Credible exclusives, leaks and scoops
- Major studio or publisher acquisitions, layoffs, funding and shutdowns
- Platform-defining events such as Nintendo Direct, State of Play, Xbox Showcase and The Game Awards
- Security incidents, account compromises and major service outages
- Significant pricing, subscription, storefront and monetization changes
- Hardware and performance developments with broad gaming impact

It deprioritizes:

- Routine patch notes and minor balance changes
- Promotional fluff
- Duplicate coverage of the same event
- Opinion-only articles, retrospectives and listicles
- Reviews, unboxings and first-look pieces with no new factual development
- Unsupported rumors
- Podcast/video/event recordings without article-level news
- General consumer technology not materially related to gaming
- Esports match recaps unless they have wider industry significance

## Editorial Scoring

Every candidate receives a 0-10 score.

| Score | Editorial tier | Meaning |
|---|---|---|
| 9-10 | Exceptional | Major launches, massive outages/security incidents, industry-changing acquisitions and major moves by Sony, Microsoft, Nintendo, Valve, Epic, Tencent, EA, Take-Two, Ubisoft or Activision Blizzard |
| 7-8 | Clearly important | Publish threshold. Significant updates, major launches/expansions, meaningful platform or monetization changes, security/anti-cheat changes, acquisitions/closures and notable industry incidents |
| 4-6 | Interesting, not enough | Minor announcements, routine updates, niche stories and low-impact developments |
| 0-3 | Low importance | Clickbait, unsupported rumors, promotions, duplicate coverage, routine version bumps and opinion-only material |

`important = true` is derived strictly from `score >= 7`.

Modifiers are supporting signals, not a separate quota system:

- **Exclusivity:** +0.5 to +1.5 only when the base score is already at least 5.
- **Trending:** +0 to +0.5 when multiple distinct outlets cover the same event.
- **Confidence:** `confirmed` for official/established reporting, `unconfirmed` for credible leaks or insider reports.

## 72-Hour Safety Net

Each daily run fetches the previous 72 hours:

```text
0-24h     -> primary
24-72h    -> catch-up
72h+      -> excluded
```

This gives a missed run a second chance without turning the channel into an archive.

Stories recovered from the 24-72 hour bucket are marked `🕓 Catch-up`.

`posted_urls.txt` and the event state prevent already-published stories from being sent again.

## Source Strategy

### Primary 20-source whitelist

The primary universe follows the source list defined for GamingNewsroom V1:

1. VGC
2. Insider Gaming
3. Gematsu
4. IGN
5. GameSpot
6. Eurogamer
7. GamesIndustry.biz
8. PC Gamer
9. GamesRadar+
10. Polygon
11. Kotaku
12. VG247
13. Destructoid
14. Rock Paper Shotgun
15. Digital Foundry
16. Nintendo Life
17. Push Square
18. Pure Xbox
19. Pocket Gamer
20. Game Developer

The implementation uses **source-scoped Google News RSS feeds** for the 20-source collection layer. This avoids depending on publisher-specific RSS endpoints that can change or disappear. Results are resolved to the original publisher URL before they enter the queue.

### Thin-day fallback

Fallback sources are opened only when fewer than **3 primary stories** clear the 7/10 publish threshold.

Fallback list:

- TheGamer
- Siliconera
- Wccftech
- TouchArcade
- Game Rant

Fallback articles are scored using the same rubric and can publish only when they clear the same threshold.

## Discovery Architecture

```text
20-source RSS discovery
        +
Google News RSS gap fill
        +
Exa news discovery
        |
        v
72-hour candidate queue
        |
        v
Source validation
        |
        v
Freshness bucketing
        |
        v
URL/title/event deduplication
        |
        v
Importance scoring 0-10
        |
        v
Threshold gate >= 7
        |
        v
Event clustering
        |
        v
Article extraction
        |
        v
Story generation
        |
        v
Numeric + claim verification
        |
        v
Image processing
        |
        v
Telegram Rich Message
        |
        v
Persist state
```

## Event Clustering and Deduplication

Multiple articles about the same event are clustered using:

- Stable `event_key` values from the editorial classifier
- Title similarity
- Entity overlap
- Publication-time proximity

One representative story is kept per event cluster.

Publication history also checks canonical URLs and recently published event headlines.

## Article Processing

The selected source article is processed through:

- Full article extraction with Trafilatura
- Exa content fallback when local extraction fails
- Source text cleanup
- Structured Cerebras story generation
- Numeric grounding
- Material claim verification
- Event/state tracking
- Image discovery and branding
- Telegram Rich Message publication

The generation layer is instructed not to invent names, dates, numbers, platforms, studios or other factual claims.

## Telegram Publication Format

Each GamingNewsroom post contains:

1. Image
2. Headline
3. Badge: `Exclusive`, `Breaking`, `Confirmed`, `Unconfirmed`, or `🕓 Catch-up`
4. Relevant platform tags
5. One-line summary
6. Key Highlights
7. What to Know
8. Hashtags
9. Source
10. Original article link

The image carries a small `@GamingNewsroom` brand mark.

## State Management

Persistent files:

```text
gaming_state.json
posted_urls.txt
```

`gaming_state.json` stores:

- Candidate queue
- Feed health
- Event clusters
- Published event IDs
- Recent headlines
- Published event records
- Category coverage

`posted_urls.txt` stores canonical publication history.

**Do not overwrite these files when deploying a new `main.py`.**

## GitHub Actions

The workflow runs once per day at:

```text
13:00 UTC
19:00 Asia/Dhaka
```

Manual execution remains available through `workflow_dispatch`.

The workflow:

1. Checks out the repository
2. Installs Python 3.12
3. Installs dependencies
4. Runs a syntax check
5. Runs the self-test
6. Executes `main.py`
7. Commits state updates back to the repository

## Required GitHub Secrets

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

## Environment Variables

```text
EXA_API_KEY
CEREBRAS_API_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_CHANNEL=@GamingNewsroom
NEWS_MODE=update
PYTHONUNBUFFERED=1
```

## Project Structure

```text
GamingNewsroom/
|
├── main.py
├── requirements.txt
├── gaming_state.json
├── posted_urls.txt
├── README.md
|
└── .github/
    └── workflows/
        └── newbot.yml
```

## Local Run

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt

export EXA_API_KEY="..."
export CEREBRAS_API_KEY="..."
export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_CHANNEL="@GamingNewsroom"
export NEWS_MODE="update"

python main.py
```

Self-test:

```bash
python main.py --self-test
```

## Operational Principles

1. **Threshold, not quota.** Publish because a story scores at least 7/10.
2. **Daily schedule.** Run once every 24 hours.
3. **72-hour hard ceiling.** Missed stories can be recovered only inside 72 hours.
4. **Persistent publication history.** Canonical URLs and events prevent duplicate sends.
5. **Trust-weighting through source context.** Source coverage informs confidence and exclusivity evaluation, not an artificial source leaderboard.
6. **No forced source rotation.** The best stories publish regardless of publisher.
7. **Leaks are labeled.** Unconfirmed reports stay explicitly unconfirmed.
8. **No invented facts.** Generation stays grounded in source material.
9. **Circuit breaker only for engineering safety.** Forty stories is not an editorial target.
10. **Stable publication structure.** Story volume changes, but the Telegram card format does not.

## Design Philosophy

> **Publish everything that earns its place in a day, nothing that does not, and give missed news one honest chance to catch up before it ages out.**
