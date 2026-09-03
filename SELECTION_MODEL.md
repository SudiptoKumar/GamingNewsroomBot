# GamingNewsroom Selection Model

## Purpose

`selection_engine.py` contains the editorial selection policy for GamingNewsroom. `main.py` orchestrates discovery, article processing, image handling, and Telegram delivery.

## Selection flow

1. Normalize and collect current gaming candidates.
2. Cluster likely copies of the same underlying event.
3. Preserve all cluster members for evidence, but select one strongest representative.
4. Give every event cluster an AI-generated importance score from 0 to 100.
5. Reject scores below `PUBLISH_SCORE_THRESHOLD` (default 80).
6. Rank qualifying events by importance, then multi-source confirmation, editorial rank, and freshness.
7. Apply `MAX_POSTS_PER_RUN` only as a safety ceiling. It is never a target.
8. Process ranked representatives through article extraction, generation, grounding, verification, and final duplicate checks.
9. Publish only stories that survive downstream quality checks.

## Score model

The gaming-specific editor considers:

- event significance
- relevance to gamers and the GamingNewsroom audience
- real-world player/platform impact
- timeliness, without allowing freshness alone to dominate
- source authority
- evidence strength
- originality/new information
- multi-source confirmation
- audience value
- material change versus previously published coverage

Penalties apply for duplicate/repetitive coverage, trivial or routine news, unsupported rumors/speculation, promotional material, opinion-only coverage, and niche stories with limited audience value.

## Material change

A previously covered event can qualify again when a candidate contains a meaningful new fact, decision, outcome, availability change, release change, security development, business action, player impact, or other material development.

A rewrite, summary, reaction, recap, or commentary-only follow-up should score below the publication threshold.

## Safety ceiling

`MAX_POSTS_PER_RUN` prevents an abnormal news cycle from flooding Telegram. The engine never tries to fill the ceiling. If only two stories score at least 80, only those two enter the publishable set.
