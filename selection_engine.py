"""Gaming editorial selection and ranking engine.

The selection engine owns the business rules for candidate clustering, 0-100
editorial scoring, publishability thresholds, and the per-run safety ceiling.
It deliberately has no Telegram, RSS, or API-key responsibilities.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_RANKING_BATCH_SIZE = 35
RANKING_RECOVERY_MULTIPLIER = 2
DEFAULT_EVENT_RETENTION_DAYS = 30

RANK_SCHEMA = {
    "type": "object",
    "properties": {
        "ranked": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "rank": {"type": "integer", "minimum": 1},
                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                    "important": {"type": "boolean"},
                    "topic": {"type": "string"},
                    "institution": {"type": "string"},
                    "event_key": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "rank", "score", "important", "topic", "institution", "event_key", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["ranked"],
    "additionalProperties": False,
}


class SelectionEngine:
    """Own the gaming-news ranking and publication-selection policy."""

    AUTHORITY_ORDER = {name: index for index, name in enumerate([
        "VGC", "Gematsu", "Game Developer", "Insider Gaming", "IGN", "GameSpot",
        "Eurogamer", "PC Gamer", "Polygon", "Nintendo Life", "Push Square",
        "Pure Xbox", "Shacknews", "VG247", "GamesRadar+", "Rock Paper Shotgun",
        "Kotaku", "Siliconera", "TechRaptor", "The Escapist",
    ])}

    def __init__(
        self,
        *,
        state: Dict[str, Any],
        now_provider: Callable[[], datetime],
        ai_create: Callable[..., Any],
        canonical_topic: Callable[[str, str], str],
        title_similarity: Callable[[str, str], float],
        event_similarity: Callable[[Dict[str, Any], Dict[str, Any]], float],
        same_event_window: Callable[..., bool],
        normalize_title: Callable[[str], str],
        safe_text: Callable[[Any], str],
        trim_source_text: Callable[[str, int], str],
        parse_datetime: Callable[[Any], Optional[datetime]],
        persist_event_cluster_state: Callable[[List[Dict[str, Any]]], None],
        topics: List[str],
        publish_score_threshold: int = 80,
        max_posts_per_run: int = 20,
        ranking_batch_size: int = DEFAULT_RANKING_BATCH_SIZE,
        event_retention_days: int = DEFAULT_EVENT_RETENTION_DAYS,
        logger_obj: Optional[logging.Logger] = None,
    ) -> None:
        self.state = state
        self.now_provider = now_provider
        self.ai_create = ai_create
        self.canonical_topic = canonical_topic
        self.title_similarity = title_similarity
        self.event_similarity = event_similarity
        self.same_event_window = same_event_window
        self.normalize_title = normalize_title
        self.safe_text = safe_text
        self.trim_source_text = trim_source_text
        self.parse_datetime = parse_datetime
        self.persist_event_cluster_state = persist_event_cluster_state
        self.topics = topics
        self.publish_score_threshold = int(publish_score_threshold)
        self.max_posts_per_run = int(max_posts_per_run)
        self.ranking_batch_size = max(1, int(ranking_batch_size))
        self.event_retention_days = int(event_retention_days)
        self.logger = logger_obj or logger

    def precluster_candidates(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Cluster likely copies of the same event before final scoring."""
        clusters: List[List[Dict[str, Any]]] = []
        ordered = sorted(
            candidates,
            key=lambda x: self.parse_datetime(x.get("published_date"))
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        for item in ordered:
            placed = False
            for cluster in clusters:
                representative = cluster[0]
                same_existing_key = bool(
                    self.safe_text(item.get("event_key"))
                    and self.safe_text(item.get("event_key")) == self.safe_text(representative.get("event_key"))
                )
                if same_existing_key or (
                    self.same_event_window(item, representative, hours=36)
                    and self.event_similarity(item, representative) >= 0.88
                ):
                    cluster.append(item)
                    placed = True
                    break
            if not placed:
                clusters.append([item])

        output = []
        for cluster in clusters:
            cluster_sorted = sorted(
                cluster,
                key=lambda x: (
                    -len(self.safe_text(x.get("excerpt", ""))),
                    -(self.parse_datetime(x.get("published_date")).timestamp()
                      if self.parse_datetime(x.get("published_date")) else 0),
                ),
            )
            sources = sorted({self.safe_text(x.get("source")) for x in cluster if self.safe_text(x.get("source"))})
            stable = self.normalize_title(cluster[0].get("title", "")) or cluster[0].get("canonical", "")
            cluster_id = f"evt_{hashlib.sha1(stable.encode('utf-8')).hexdigest()[:10]}"
            rep = cluster_sorted[0]
            output.append({
                "cluster_id": cluster_id,
                "members": cluster,
                "representative": rep,
                "source_count": len(sources),
                "sources": sources,
                "latest_published_date": max(
                    (self.parse_datetime(x.get("published_date")) for x in cluster if self.parse_datetime(x.get("published_date"))),
                    default=None,
                ),
                "titles": [self.safe_text(x.get("title")) for x in cluster if self.safe_text(x.get("title"))][:5],
                "excerpts": [self.trim_source_text(x.get("excerpt", ""), 500) for x in cluster if self.safe_text(x.get("excerpt", ""))][:3],
            })
        return output

    def published_event_context(self, cluster: Dict[str, Any], max_items: int = 3) -> List[Dict[str, Any]]:
        context = []
        now = self.now_provider()
        for event in self.state.get("events", {}).values():
            if event.get("status") != "published":
                continue
            published_at = self.parse_datetime(event.get("published_at"))
            if not published_at or (now - published_at).total_seconds() > self.event_retention_days * 86400:
                continue
            previous = {"title": event.get("headline", ""), "summary": event.get("summary", "")}
            similarity = max(
                [self.title_similarity(previous["title"], title) for title in cluster.get("titles", [])] or [0.0]
            )
            if similarity >= 0.72:
                context.append({
                    "similarity": similarity,
                    "headline": self.safe_text(event.get("headline")),
                    "summary": self.trim_source_text(event.get("summary", ""), 320),
                    "published_at": published_at.isoformat(),
                    "event_cluster_id": self.safe_text(event.get("event_cluster_id")),
                })
        context.sort(key=lambda x: x["similarity"], reverse=True)
        return context[:max_items]

    def select_cluster_representative(self, cluster: Dict[str, Any]) -> Dict[str, Any]:
        return sorted(
            cluster.get("members", []),
            key=lambda x: (
                self.AUTHORITY_ORDER.get(self.safe_text(x.get("source")), 999),
                -(self.parse_datetime(x.get("published_date")).timestamp() if self.parse_datetime(x.get("published_date")) else 0),
                -len(self.safe_text(x.get("excerpt", ""))),
            ),
        )[0]

    def rank_event_clusters(self, clusters: List[Dict[str, Any]], region: str) -> List[Dict[str, Any]]:
        if not clusters:
            return []
        all_rows = []
        topic_list = ", ".join(self.topics)
        now = self.now_provider()
        for cluster in clusters:
            rep = self.select_cluster_representative(cluster)
            previous = self.published_event_context(cluster)
            latest = cluster.get("latest_published_date")
            age_hours = max(0.0, (now - latest).total_seconds() / 3600) if latest else 9999
            previous_block = "None found."
            if previous:
                previous_block = "\n".join(
                    f"- {x['headline']} | {x['published_at']} | similarity={x['similarity']:.2f}\n  {x['summary']}"
                    for x in previous
                )
            all_rows.append({
                "cluster": cluster,
                "payload": "\n".join([
                    f"CLUSTER_ID: {cluster['cluster_id']}",
                    f"Representative title: {rep.get('title','')}",
                    f"Representative source: {rep.get('source','')}",
                    f"Sources reporting this event: {', '.join(cluster.get('sources', []))}",
                    f"Source count: {cluster.get('source_count', 0)}",
                    f"Age: {age_hours:.1f} hours",
                    f"Candidate titles in cluster: {' | '.join(cluster.get('titles', []))}",
                    f"Evidence excerpts: {' | '.join(cluster.get('excerpts', []))}",
                    f"Previously published related coverage:\n{previous_block}",
                ]),
            })

        ranked_rows = []
        for offset in range(0, len(all_rows), self.ranking_batch_size):
            batch = all_rows[offset:offset + self.ranking_batch_size]
            prompt = f"""
You are the senior editor-in-chief of @GamingNewsroom, a gaming news channel for players and gaming enthusiasts.

Score EVERY event cluster from 0 to 100 and rank the clusters from most important to least important.
The score is the editorial importance of the underlying event, not how exciting the headline sounds.
There is NO quota and NO target number of publications. A low-value story must never receive a high score merely
because the channel has room for another post.

SCORING FACTORS
- Importance and event significance
- Relevance to gamers and this gaming-news audience
- Real-world player or platform impact
- Timeliness/freshness, but never freshness alone
- Source quality and authority
- Evidence strength
- Originality/new information
- Multi-source confirmation when available
- Audience value
- Material change compared with previously published coverage

PENALTIES
- Duplicate/repetitive event coverage
- Minor or trivial news
- Routine patches/maintenance unless unusually important
- Unsupported rumors, speculation, leaks, or promotional material
- Opinion-only coverage without a concrete new event
- Niche stories with little audience value
- Articles that merely rewrite or react to an already-published event without substantial new information

MATERIAL CHANGE RULE
If a previously published related story exists, score the NEW DEVELOPMENT, not the old event.
A candidate remains publishable only when it contains a meaningful new fact, decision, outcome, availability change,
release change, security development, business action, player impact, or other material development.
Mere rewrites, summaries, reactions, recaps, or added commentary should score below the publication threshold.

0-19 = negligible / routine / unusable
20-39 = low importance
40-59 = modest / interesting
60-79 = meaningful but below main-channel threshold
80-89 = clearly important and publishable
90-100 = exceptional, major, industry/player-impacting news

IMPORTANT MUST be true ONLY when score >= {self.publish_score_threshold}.
When uncertain, choose the lower score.
Return EVERY cluster, even low-scoring clusters.
In each returned object, put the exact CLUSTER_ID from the input in the `id` field.
Use `rank` only as the within-batch order; the application performs the final global sort.
Allowed topic taxonomy:
{topic_list}
"""
            user = "\n\n".join(f"{idx + 1}. {row['payload']}" for idx, row in enumerate(batch))
            try:
                response = self.ai_create(
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user},
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": "gaming_news_rank_v2",
                            "strict": True,
                            "schema": RANK_SCHEMA,
                        },
                    },
                    reasoning_effort="low",
                    temperature=0.0,
                    max_completion_tokens=max(1200, len(batch) * 110),
                )
                data = json.loads(self.safe_text(response.choices[0].message.content))
                by_id = {row["cluster"]["cluster_id"]: row for row in batch}
                returned = set()
                for item in data.get("ranked", []):
                    cid = self.safe_text(item.get("id"))
                    if cid not in by_id:
                        continue
                    score = max(0, min(100, int(item.get("score", 0))))
                    source_row = by_id[cid]
                    ranked_rows.append({
                        "cluster": source_row["cluster"],
                        "score": score,
                        "important": score >= self.publish_score_threshold,
                        "topic": self.canonical_topic(self.safe_text(item.get("topic")), region),
                        "institution": self.safe_text(item.get("institution")),
                        "reason": self.safe_text(item.get("reason")),
                    })
                    returned.add(cid)
                for row in batch:
                    cid = row["cluster"]["cluster_id"]
                    if cid not in returned:
                        ranked_rows.append({
                            "cluster": row["cluster"], "score": 0, "important": False,
                            "topic": self.canonical_topic("", region), "institution": "",
                            "reason": "Ranking response omitted this event; withheld for safety.",
                        })
            except Exception as exc:
                self.logger.error("Editorial ranking batch failed for %s: %s", region, type(exc).__name__)
                for row in batch:
                    ranked_rows.append({
                        "cluster": row["cluster"], "score": 0, "important": False,
                        "topic": self.canonical_topic("", region), "institution": "",
                        "reason": "Ranking-service failure; candidate withheld to avoid publishing unscored news.",
                    })

        ranked_rows.sort(key=lambda x: (
            -x["score"],
            -x["cluster"].get("source_count", 0),
            -(x["cluster"].get("latest_published_date").timestamp() if x["cluster"].get("latest_published_date") else 0),
        ))

        output = []
        for rank, entry in enumerate(ranked_rows, start=1):
            cluster = entry["cluster"]
            selected_rep = self.select_cluster_representative(cluster)
            event_id = cluster["cluster_id"]
            member_ids = {self.safe_text(x.get("canonical")) for x in cluster.get("members", [])}
            for member in cluster.get("members", []):
                row = dict(member)
                row.update({
                    "editor_rank": rank,
                    "importance_score": entry["score"],
                    "important": entry["important"],
                    "topic": entry["topic"] or self.canonical_topic(member.get("topic", ""), region),
                    "institution": entry["institution"],
                    "event_key": event_id,
                    "event_cluster_id": event_id,
                    "event_cluster_size": len(cluster.get("members", [])),
                    "event_sources": cluster.get("sources", []),
                    "event_source_count": cluster.get("source_count", 0),
                    "event_confidence": 1.0 if cluster.get("source_count", 0) > 1 else 0.6,
                    "rank_reason": entry["reason"],
                    "event_member_ids": sorted(member_ids),
                    "selected_representative": self.safe_text(member.get("canonical")) == self.safe_text(selected_rep.get("canonical")),
                })
                output.append(row)

            self.persist_event_cluster_state([{
                **selected_rep,
                "event_cluster_id": event_id,
                "event_sources": cluster.get("sources", []),
                "event_source_count": cluster.get("source_count", 0),
                "event_confidence": 1.0 if cluster.get("source_count", 0) > 1 else 0.6,
                "topic": entry["topic"],
                "importance_score": entry["score"],
            }])
        return output

    def rank_candidates(self, candidates: List[Dict[str, Any]], region: str) -> List[Dict[str, Any]]:
        if not candidates:
            return []
        return self.rank_event_clusters(self.precluster_candidates(candidates), region)

    def build_candidate_pool(self, ranked: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not ranked:
            return []
        pool_size = max(self.max_posts_per_run, self.max_posts_per_run * RANKING_RECOVERY_MULTIPLIER)
        return [dict(item) for item in ranked[:pool_size]]

    def select_publishable(self, ranked: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Return eligible ranked representatives, capped only by the safety ceiling."""
        eligible = [
            x for x in ranked
            if int(x.get("importance_score", 0)) >= self.publish_score_threshold
            and bool(x.get("important", False))
        ]
        eligible.sort(key=lambda x: (
            -int(x.get("importance_score", 0)),
            -int(x.get("event_source_count", 0)),
            int(x.get("editor_rank", 999999)),
        ))
        return eligible[: self.max_posts_per_run]
