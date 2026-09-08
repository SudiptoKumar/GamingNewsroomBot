from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Callable

logger = logging.getLogger("gaming-news-bot.event-engine")

SOURCE_TIERS = {
    "VGC": 1, "Gematsu": 1, "Game Developer": 1, "Insider Gaming": 1,
    "IGN": 2, "GameSpot": 2, "Eurogamer": 2, "PC Gamer": 2, "Polygon": 2,
    "Nintendo Life": 2, "Push Square": 2, "Pure Xbox": 2, "Shacknews": 2,
    "VG247": 2, "GamesRadar+": 3, "Rock Paper Shotgun": 3, "Kotaku": 3,
    "Siliconera": 3, "TechRaptor": 3, "The Escapist": 3,
}

EVENT_TYPES = {
    "announcement", "release", "delay", "cancellation", "update", "patch", "expansion",
    "dlc", "acquisition", "merger", "closure", "layoff", "pricing", "subscription",
    "platform_change", "platform_outage", "security", "esports", "milestone", "reveal",
    "business", "legal", "rumor", "denial", "review", "other",
}

MODALITIES = {"confirmed", "reported", "rumored", "speculative", "denied", "forecast", "unknown"}

IDENTITY_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "minimum": 1},
                    "event_key": {"type": "string"},
                    "subject": {"type": "string"},
                    "event_type": {"type": "string"},
                    "action": {"type": "string"},
                    "status": {"type": "string"},
                    "modality": {"type": "string"},
                    "game": {"type": "string"},
                    "franchise": {"type": "string"},
                    "institution": {"type": "string"},
                    "target": {"type": "string"},
                    "platforms": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                    "claim": {"type": "string"},
                    "topic": {"type": "string"},
                },
                "required": [
                    "id", "event_key", "subject", "event_type", "action", "status", "modality",
                    "game", "institution", "target", "platforms", "claim", "topic",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

SIGNIFICANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "minimum": 1},
                    "significance": {"type": "integer", "minimum": 0, "maximum": 45},
                    "reason": {"type": "string"},
                },
                "required": ["id", "significance", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

MATERIAL_SCHEMA = {
    "type": "object",
    "properties": {
        "same_event": {"type": "boolean"},
        "material_change": {"type": "boolean"},
        "new_claims": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "reason": {"type": "string"},
    },
    "required": ["same_event", "material_change", "new_claims", "reason"],
    "additionalProperties": False,
}


def _text(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _norm(text: Any) -> str:
    s = _text(text).lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(text: Any) -> set[str]:
    stop = {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "from", "with",
        "by", "at", "as", "is", "are", "was", "were", "be", "been", "this", "that",
        "it", "its", "new", "game", "games", "gaming", "news", "will", "has", "have",
        "had", "about", "after", "before", "over", "more", "says", "said", "report",
        "reports", "reportedly", "according", "gets", "get", "update",
    }
    return {x for x in _norm(text).split() if len(x) >= 3 and x not in stop}


def similarity(a: Any, b: Any) -> float:
    aa, bb = _norm(a), _norm(b)
    if not aa or not bb:
        return 0.0
    seq = SequenceMatcher(None, aa, bb).ratio()
    ta, tb = _tokens(aa), _tokens(bb)
    jac = len(ta & tb) / max(1, len(ta | tb))
    return 0.55 * seq + 0.45 * jac


def source_tier(source: str) -> int:
    return SOURCE_TIERS.get(_text(source), 4)


def parse_dt(value: Any):
    raw = _text(value)
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def freshness_score(latest_published: Any, now_dt: Any) -> int:
    dt = parse_dt(latest_published)
    now = now_dt if isinstance(now_dt, datetime) else parse_dt(now_dt)
    if not dt or not now:
        return 4
    hours = max(0.0, (now - dt).total_seconds() / 3600.0)
    if hours <= 3: return 10
    if hours <= 8: return 9
    if hours <= 16: return 7
    if hours <= 24: return 6
    if hours <= 48: return 4
    if hours <= 72: return 2
    return 1


def _source_trust(sources: list[str]) -> int:
    if not sources:
        return 0
    tiers = [source_tier(s) for s in sources]
    best = min(tiers)
    base = {1: 10, 2: 8, 3: 6, 4: 3}.get(best, 3)
    if best == 1 and len(set(sources)) >= 2:
        return 10
    if best == 2 and len(set(sources)) >= 2:
        return 9
    return base


def _effective_independent_sources(cluster: dict[str, Any]) -> int:
    articles = cluster.get("articles", [])
    sources = list(dict.fromkeys(_text(a.get("source")) for a in articles if _text(a.get("source"))))
    if len(sources) <= 1:
        return len(sources)
    # Penalize obvious rewrites with nearly identical titles. Distinct publishers remain distinct
    # evidence unless their headlines are effectively clones.
    groups: list[list[str]] = []
    for article in articles:
        src = _text(article.get("source"))
        title = article.get("title")
        if not src:
            continue
        placed = False
        for group in groups:
            exemplar = next((a for a in articles if _text(a.get("source")) == group[0]), None)
            if exemplar and similarity(title, exemplar.get("title")) >= 0.94:
                if src not in group:
                    group.append(src)
                placed = True
                break
        if not placed:
            groups.append([src])
    # At minimum, one independent signal per distinct source cluster, capped by unique publishers.
    return max(1, min(len(sources), len(groups)))


def corroboration_score(independent_sources: int) -> int:
    return {0: 0, 1: 2, 2: 6, 3: 10, 4: 14, 5: 17}.get(min(independent_sources, 5), 20)


def originality_score(cluster: dict[str, Any]) -> int:
    articles = cluster.get("articles", [])
    if not articles:
        return 0
    ordered = sorted(articles, key=lambda a: parse_dt(a.get("first_seen_at") or a.get("published_date")) or datetime.max.replace(tzinfo=timezone.utc))
    first = ordered[0]
    source = _text(first.get("source"))
    best_tier = source_tier(source)
    points = {1: 12, 2: 10, 3: 8, 4: 5}.get(best_tier, 5)
    if _text(first.get("is_primary_source")) == "true" or first.get("primary_source") is True:
        points += 3
    if len(articles) >= 2:
        later = articles[1:]
        citations = sum(1 for a in later if source.lower() and source.lower() in _text(a.get("excerpt")).lower())
        points += min(3, citations)
    return min(15, points)


def representative_key(item: dict[str, Any]) -> tuple:
    tier = source_tier(_text(item.get("source")))
    dt = parse_dt(item.get("published_date"))
    excerpt_len = len(_text(item.get("excerpt")))
    return (tier, -(dt.timestamp() if dt else 0), -excerpt_len)


def choose_representative(cluster: dict[str, Any]) -> dict[str, Any]:
    articles = list(cluster.get("articles", []))
    return sorted(articles, key=representative_key)[0] if articles else {}


def _fallback_identity(item: dict[str, Any]) -> dict[str, Any]:
    title = _text(item.get("title"))
    excerpt = _text(item.get("excerpt"))
    low = f"{title} {excerpt}".lower()
    event_type = "other"
    mapping = [
        ("acquisition", "acquisition"), ("acquires", "acquisition"), ("merger", "merger"),
        ("layoff", "layoff"), ("job cuts", "layoff"), ("shutdown", "closure"), ("closed", "closure"),
        ("delay", "delay"), ("delayed", "delay"), ("canceled", "cancellation"), ("cancelled", "cancellation"),
        ("release", "release"), ("launch", "release"), ("update", "update"), ("patch", "patch"),
        ("expansion", "expansion"), ("dlc", "dlc"), ("trailer", "reveal"), ("screenshots", "reveal"),
        ("sales", "milestone"), ("million", "milestone"), ("breach", "security"), ("outage", "platform_outage"),
        ("rumor", "rumor"), ("reportedly", "rumor"), ("denies", "denial"),
    ]
    for marker, value in mapping:
        if marker in low:
            event_type = value
            break
    subject = title
    for marker in (" announces ", " announced ", " reveals ", " revealed ", " launches ", " delays ", " delayed "):
        if marker in title.lower():
            subject = title[:title.lower().index(marker)]
            break
    subject = re.sub(r"\s+", " ", subject).strip(" -:|") or title
    modality = "rumored" if event_type == "rumor" else ("denied" if event_type == "denial" else "confirmed")
    return {
        "event_key": _norm(f"{subject} {event_type}"), "subject": subject, "event_type": event_type,
        "action": event_type, "status": "current", "modality": modality, "game": subject, "franchise": subject,
        "institution": _text(item.get("institution")), "target": subject, "platforms": [],
        "claim": _text(item.get("excerpt"))[:400], "topic": _text(item.get("topic")) or "Gaming",
    }


SLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "selected_ids": {"type": "array", "items": {"type": "integer", "minimum": 1}, "maxItems": 20},
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "minimum": 1},
                    "decision": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "decision", "reason"],
                "additionalProperties": False,
            },
            "maxItems": 40,
        },
    },
    "required": ["selected_ids", "decisions"],
    "additionalProperties": False,
}

class EventEngine:
    def __init__(self, ai_create: Callable[..., Any], now_dt: Any, threshold: int = 80, batch_size: int = 35):
        self.ai_create = ai_create
        self.now_dt = now_dt
        self.threshold = threshold
        self.batch_size = batch_size

    def _ai_json(self, *, system: str, user: str, schema: dict, name: str, tokens: int = 4500) -> dict:
        response = self.ai_create(
            model_name=None,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
            reasoning_effort="low", temperature=0.0, max_completion_tokens=tokens,
        )
        return json.loads(_text(response.choices[0].message.content))

    def identify(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = [dict(x) for x in candidates]
        for start in range(0, len(result), self.batch_size):
            batch = result[start:start + self.batch_size]
            blocks = []
            for i, item in enumerate(batch, 1):
                blocks.append("\n".join([
                    f"ID: {i}", f"Title: {_text(item.get('title'))}", f"Source: {_text(item.get('source'))}",
                    f"Published: {_text(item.get('published_date'))}", f"Excerpt: {_text(item.get('excerpt'))[:1400]}",
                ]))
            system = """
You are the event-identity editor for a gaming newsroom.
Identify the underlying REAL NEWS EVENT for each article. The task is not headline similarity.
Return a stable event identity using the concrete game/franchise/company, core action, target, event type and modality.
Articles about screenshots, videos, previews, hands-on reports or commentary may belong to the same event
when they are coverage of the same underlying development. Do NOT merge merely because the same game,
franchise or company is mentioned. Rumor, denial and confirmed developments must keep distinct modalities.
Use concise canonical event_key values. Prefer the actual event over article-specific phrasing.
Return exactly one item for every ID.
"""
            try:
                data = self._ai_json(system=system, user="\n\n".join(blocks), schema=IDENTITY_SCHEMA, name="gaming_event_identity_v2", tokens=6000)
                by_id = {int(x["id"]): x for x in data.get("items", [])}
                for i, item in enumerate(batch, 1):
                    row = by_id.get(i) or _fallback_identity(item)
                    row["event_type"] = row.get("event_type") if row.get("event_type") in EVENT_TYPES else "other"
                    row["modality"] = row.get("modality") if row.get("modality") in MODALITIES else "unknown"
                    item.update(row)
            except Exception as exc:
                logger.warning("V3 identity batch failed; deterministic fallback: %s", type(exc).__name__)
                for item in batch:
                    item.update(_fallback_identity(item))
        return result

    @staticmethod
    def _cannot_link(a: dict[str, Any], b: dict[str, Any]) -> bool:
        if _norm(a.get("modality")) and _norm(b.get("modality")):
            if {_norm(a.get("modality")), _norm(b.get("modality"))} == {"confirmed", "denied"}:
                return True
        if _norm(a.get("event_type")) == "rumor" and _norm(b.get("event_type")) in {"release", "announcement", "delay", "cancellation"}:
            return True
        if _norm(a.get("event_type")) == "denial" and _norm(b.get("modality")) == "confirmed":
            return True
        if _text(a.get("game")) and _text(b.get("game")):
            if similarity(a.get("game"), b.get("game")) < 0.30:
                return True
        return False

    def _same_event_pair(self, a: dict[str, Any], b: dict[str, Any]) -> bool:
        if self._cannot_link(a, b):
            return False
        ak, bk = _norm(a.get("event_key")), _norm(b.get("event_key"))
        if ak and bk and ak == bk:
            return True
        fields = ["game", "institution", "event_type", "action", "target"]
        matches = 0
        for field in fields:
            av, bv = _text(a.get(field)), _text(b.get(field))
            if av and bv and similarity(av, bv) >= 0.78:
                matches += 1
        claim_sim = similarity(a.get("claim"), b.get("claim"))
        subj_sim = similarity(a.get("subject"), b.get("subject"))
        type_match = _norm(a.get("event_type")) == _norm(b.get("event_type"))
        modality_match = _norm(a.get("modality")) == _norm(b.get("modality")) or "unknown" in {_norm(a.get("modality")), _norm(b.get("modality"))}
        score = 0.35 * subj_sim + 0.25 * claim_sim + 0.20 * (matches / 5.0) + 0.10 * float(type_match) + 0.10 * float(modality_match)
        return score >= 0.62

    def cluster(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        clusters: list[dict[str, Any]] = []
        for item in candidates:
            assigned = None
            for cluster in clusters:
                # Do not let a single broad event_key swallow everything. Pair against up to 3 representatives.
                anchors = cluster["articles"][:3]
                if any(self._same_event_pair(item, anchor) for anchor in anchors):
                    assigned = cluster
                    break
            if assigned is None:
                clusters.append({"articles": [dict(item)]})
            else:
                assigned["articles"].append(dict(item))

        output = []
        for cluster in clusters:
            rep = choose_representative(cluster)
            sources = list(dict.fromkeys(_text(a.get("source")) for a in cluster["articles"] if _text(a.get("source"))))
            event_key = _norm(rep.get("event_key")) or _norm(rep.get("title"))
            frame = {
                "event_key": event_key,
                "subject": _text(rep.get("subject")) or _text(rep.get("title")),
                "event_type": _text(rep.get("event_type")) or "other",
                "action": _text(rep.get("action")), "status": _text(rep.get("status")),
                "modality": _text(rep.get("modality")) or "unknown", "game": _text(rep.get("game")),
                "franchise": _text(rep.get("franchise")) or _text(rep.get("game")),
                "institution": _text(rep.get("institution")), "target": _text(rep.get("target")),
                "platforms": rep.get("platforms", []), "topic": _text(rep.get("topic")) or "Gaming",
            }
            output.append({
                "cluster_id": f"evt_{hashlib.sha1(event_key.encode()).hexdigest()[:14]}",
                "event_key": event_key,
                "event_subject": frame["subject"], "event_type": frame["event_type"],
                "topic": frame["topic"], "institution": frame["institution"], "modality": frame["modality"],
                "event_frame": frame, "representative": rep, "articles": cluster["articles"],
                "sources": sources, "source_count": len(sources),
            })
        return output

    def match_previous(self, cluster: dict[str, Any], previous_events: dict[str, Any]) -> tuple[str, dict[str, Any] | None, float]:
        best_id, best_event, best = "", None, 0.0
        frame = cluster.get("event_frame", {})
        for event_id, event in previous_events.items():
            if _text(event.get("status")) not in {"published", "published_update"}:
                continue
            if _norm(event.get("event_key")) and _norm(event.get("event_key")) == _norm(cluster.get("event_key")):
                return event_id, event, 1.0
            prev_frame = event.get("event_frame", {})
            s = 0.0
            for f, w in (("game", .25), ("franchise", .20), ("institution", .10), ("event_type", .15), ("action", .10), ("target", .10), ("modality", .03), ("subject", .07)):
                av, bv = _text(frame.get(f)), _text(prev_frame.get(f) or event.get("event_subject"))
                if av and bv:
                    s += w * similarity(av, bv)
            if s > best:
                best, best_id, best_event = s, event_id, event
        return (best_id, best_event, best)

    def material_change(self, cluster: dict[str, Any], previous: dict[str, Any]) -> tuple[bool, str, list[str]]:
        current = []
        for article in cluster.get("articles", [])[:8]:
            current.append(f"{article.get('source')} | {article.get('title')} | {_text(article.get('excerpt'))[:700]}")
        previous_claims = previous.get("claims", [])
        system = """
You are a strict continuity editor for a gaming newsroom.
Decide whether the current cluster is the same underlying event as the previously published event.
If it is the same event, decide whether there is a MATERIAL NEW CLAIM that justifies another post.
New screenshots, comparisons, previews, rewrites, commentary and recycled coverage are not material.
Material developments include confirmed release/delay/cancellation, major update or DLC, major price,
platform availability, acquisition/closure/layoff, significant security incident, major milestone,
or a new official gameplay/content fact that changes what the audience knows.
Return only the JSON schema.
"""
        previous_text = json.dumps({
            "event_key": previous.get("event_key"), "event_frame": previous.get("event_frame", {}),
            "headline": previous.get("headline"), "summary": previous.get("summary"), "claims": previous_claims,
        }, ensure_ascii=False)
        try:
            data = self._ai_json(system=system, user="PREVIOUS:\n" + previous_text + "\n\nCURRENT:\n" + "\n".join(current), schema=MATERIAL_SCHEMA, name="gaming_material_change_v2", tokens=1800)
            same = bool(data.get("same_event"))
            change = bool(data.get("material_change"))
            return same and change, _text(data.get("reason")), [x for x in data.get("new_claims", []) if _text(x)]
        except Exception as exc:
            logger.warning("V3 material-change check failed; repeat withheld: %s", type(exc).__name__)
            return False, "Continuity verification unavailable; repeat withheld for safety.", []

    def history(self, clusters: list[dict[str, Any]], previous_events: dict[str, Any]) -> list[dict[str, Any]]:
        for cluster in clusters:
            event_id, previous, match_score = self.match_previous(cluster, previous_events)
            cluster["previous_event_id"] = event_id
            cluster["history_match_score"] = round(match_score, 3)
            cluster["repeat_status"] = "new"
            cluster["repeat_reason"] = ""
            cluster["new_claims"] = []
            if previous and match_score >= 0.74:
                changed, reason, claims = self.material_change(cluster, previous)
                cluster["repeat_status"] = "material_update" if changed else "repeat"
                cluster["repeat_reason"] = reason
                cluster["new_claims"] = claims
        return clusters

    def score(self, clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not clusters:
            return []
        # AI evaluates significance only. Coverage, originality, freshness and source trust are deterministic.
        for start in range(0, len(clusters), self.batch_size):
            batch = clusters[start:start + self.batch_size]
            blocks = []
            for i, c in enumerate(batch, 1):
                evidence = []
                for a in c["articles"][:8]:
                    evidence.append(f"{a.get('source')} | {a.get('published_date')} | {a.get('title')} | {_text(a.get('excerpt'))[:500]}")
                blocks.append("\n".join([
                    f"ID: {i}", f"EVENT: {c['event_subject']}", f"GAME: {c.get('event_frame', {}).get('game', '')}", f"FRANCHISE: {c.get('event_frame', {}).get('franchise', '')}", f"TYPE: {c['event_type']}",
                    f"MODALITY: {c.get('modality','unknown')}", f"SOURCES: {', '.join(c['sources'])}",
                    "EVIDENCE:\n" + "\n".join(evidence),
                ]))
            system = """
You are the senior editor of a high-signal gaming news channel.
Score ONLY the INTRINSIC SIGNIFICANCE of each whole event from 0 to 45.
Do not reward repetition or source count here. Judge actual consequence for gamers and the gaming industry:
player impact, industry/commercial impact, magnitude, irreversibility, and unexpectedness.
Routine patches, minor balance changes, generic opinions, promotional stories, weak rumors and niche items
should normally score low. Major platform incidents, major releases, major acquisitions, major layoffs/closures,
serious security incidents, major pricing changes, and industry-changing developments score high.
Return one result per ID.
"""
            try:
                data = self._ai_json(system=system, user="\n\n".join(blocks), schema=SIGNIFICANCE_SCHEMA, name="gaming_significance_v2", tokens=5500)
                by_id = {int(x["id"]): x for x in data.get("items", [])}
            except Exception as exc:
                logger.warning("V3 significance scoring failed; using conservative fallback: %s", type(exc).__name__)
                by_id = {}
            for i, c in enumerate(batch, 1):
                row = by_id.get(i, {})
                significance = max(0, min(45, int(row.get("significance", 0) or 0)))
                independent = _effective_independent_sources(c)
                coverage = min(20, corroboration_score(independent))
                originality = originality_score(c)
                freshness = freshness_score(c["representative"].get("published_date"), self.now_dt)
                trust = _source_trust(c["sources"])
                total = min(100, significance + coverage + originality + freshness + trust + (5 if c.get("repeat_status") == "material_update" else 0))
                c.update({
                    "significance_score": significance, "coverage_score": coverage,
                    "originality_score": originality, "freshness_score": freshness,
                    "source_trust_score": trust, "independent_source_count": independent,
                    "importance_score": total, "material_update_bonus": 5 if c.get("repeat_status") == "material_update" else 0,
                    "rank_reason": _text(row.get("reason")) or "Event-level significance score.",
                    "claims": [a.get("claim") for a in c["articles"] if _text(a.get("claim"))][:10],
                })
        clusters.sort(key=lambda c: (-c.get("importance_score", 0), -c.get("significance_score", 0), -c.get("coverage_score", 0), c.get("event_subject", "")))
        for rank, c in enumerate(clusters, 1):
            c["editor_rank"] = rank
            c["publishable"] = c.get("repeat_status") != "repeat" and c.get("importance_score", 0) >= self.threshold
        return clusters

    @staticmethod
    def _identity_key(cluster: dict[str, Any], field: str) -> str:
        return _norm(cluster.get("event_frame", {}).get(field))

    @staticmethod
    def _materially_distinct(a: dict[str, Any], b: dict[str, Any]) -> bool:
        af, bf = a.get("event_frame", {}), b.get("event_frame", {})
        if _norm(a.get("event_key")) == _norm(b.get("event_key")):
            return False
        if _norm(af.get("franchise")) and _norm(af.get("franchise")) == _norm(bf.get("franchise")):
            at, bt = _norm(a.get("event_type")), _norm(b.get("event_type"))
            if at != bt and _norm(a.get("topic")) != _norm(b.get("topic")):
                return max(a.get("importance_score", 0), b.get("importance_score", 0)) >= 92
        return False

    def _hard_slate_guard(self, candidates: list[dict[str, Any]], max_posts: int) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for c in candidates:
            conflict = False
            for chosen in selected:
                same_event = _norm(c.get("event_key")) == _norm(chosen.get("event_key"))
                same_game = self._identity_key(c, "game") and self._identity_key(c, "game") == self._identity_key(chosen, "game")
                same_franchise = self._identity_key(c, "franchise") and self._identity_key(c, "franchise") == self._identity_key(chosen, "franchise")
                same_topic = bool(_norm(c.get("topic")) and _norm(chosen.get("topic")) and (
                    _norm(c.get("topic")) == _norm(chosen.get("topic"))
                    or similarity(c.get("topic"), chosen.get("topic")) >= 0.78
                ))
                if same_event:
                    conflict = True
                    break
                # Strong default: one story per game/franchise/topic in a slate.
                # Exception: genuinely exceptional, materially distinct stories.
                if (same_game or same_franchise or same_topic) and not self._materially_distinct(c, chosen):
                    conflict = True
                    break
            if not conflict:
                selected.append(c)
            if len(selected) >= max_posts:
                break
        return selected

    def _ai_slate_select(self, candidates: list[dict[str, Any]], max_posts: int) -> tuple[list[dict[str, Any]], dict[int, dict[str, str]]]:
        # The editor sees only the strongest candidates, so this is one compact AI decision rather than
        # a second LLM score for hundreds of articles.
        pool = candidates[:min(30, len(candidates))]
        if not pool:
            return [], {}
        blocks = []
        for i, c in enumerate(pool, 1):
            c["_slate_ai_id"] = i
            f = c.get("event_frame", {})
            rep = c.get("representative", {})
            blocks.append("\n".join([
                f"ID: {i}",
                f"Score: {c.get('importance_score', 0)}",
                f"Event: {c.get('event_subject', '')}",
                f"Game: {f.get('game', '')}",
                f"Franchise: {f.get('franchise', '')}",
                f"Publisher/Studio: {f.get('institution', '')}",
                f"Topic: {c.get('topic', '')}",
                f"Type: {c.get('event_type', '')}",
                f"Modality: {c.get('modality', 'unknown')}",
                f"Headline: {rep.get('title', '')}",
                f"Editorial reason: {c.get('rank_reason', '')}",
            ]))
        system = """
You are the final assigning editor for a high-signal gaming news channel.
Choose the best set of stories that should appear together in ONE run. This is slate selection, not scoring.
All candidates already passed the importance threshold. Preserve the strongest stories, but avoid editorial repetition.
Prefer broad coverage across different games, franchises, companies, platforms and underlying topics.
Treat two stories as repetitive when they concern the same game/franchise and substantially the same underlying topic,
even if they are technically different events. A second story about the same game/franchise is justified only when it
is materially different and exceptionally important. Different stories from the same company or platform are allowed.
Never select two articles representing the same event. Rumor/confirmed/denial distinctions matter.
Do not invent facts. Return only the requested JSON.
"""
        user = f"MAX STORIES: {max_posts}\n\nCANDIDATES:\n" + "\n\n".join(blocks)
        try:
            data = self._ai_json(system=system, user=user, schema=SLATE_SCHEMA, name="gaming_editorial_slate_v3", tokens=3500)
            decisions = {int(x["id"]): {"decision": _text(x.get("decision")), "reason": _text(x.get("reason"))} for x in data.get("decisions", []) if str(x.get("id", "")).isdigit()}
            selected_ids = [int(x) for x in data.get("selected_ids", []) if isinstance(x, int) and 1 <= x <= len(pool)]
            # Respect AI order; then apply a hard deterministic guard.
            ai_selected = [pool[i-1] for i in selected_ids]
            ai_selected.sort(key=lambda c: (-c.get("importance_score", 0), c.get("event_subject", "")))
            guarded = self._hard_slate_guard(ai_selected, max_posts=max_posts)
            return guarded, decisions
        except Exception as exc:
            logger.warning("V3 editorial slate AI failed; deterministic guard used: %s", type(exc).__name__)
            return self._hard_slate_guard(pool, max_posts=max_posts), {}

    def diversify(self, clusters: list[dict[str, Any]], max_posts: int = 20) -> list[dict[str, Any]]:
        eligible = [c for c in clusters if c.get("publishable")]
        if not eligible:
            return []
        # Give the editor enough alternatives to choose a diverse slate, then enforce the choice in Python.
        selected, decisions = self._ai_slate_select(eligible, max_posts=max_posts)
        selected_ids = {c.get("cluster_id") for c in selected}
        for c in eligible:
            c["slate_selected"] = c.get("cluster_id") in selected_ids
            c["slate_decision"] = decisions.get(c.get("_slate_ai_id", 0), {}).get("decision", "") if decisions else ""
            c["slate_reason"] = decisions.get(c.get("_slate_ai_id", 0), {}).get("reason", "") if decisions else ""
        for rank, c in enumerate(selected, 1):
            c["slate_rank"] = rank
            c["slate_score"] = c.get("importance_score", 0)
        logger.info("V3 EDITORIAL SLATE: eligible=%d ai_pool=%d selected=%d", len(eligible), min(30, len(eligible)), len(selected))
        for c in selected:
            logger.info("V3 SLATE #%d score=%s game=%s franchise=%s topic=%s subject=%s", c.get("slate_rank", 0), c.get("importance_score", 0), c.get("event_frame", {}).get("game", ""), c.get("event_frame", {}).get("franchise", ""), c.get("topic", ""), c.get("event_subject", ""))
        for c in eligible:
            c.pop("_slate_ai_id", None)
        return selected

    def run(self, candidates: list[dict[str, Any]], previous_events: dict[str, Any], max_posts: int = 20):
        identified = self.identify(candidates)
        clusters = self.cluster(identified)
        clusters = self.history(clusters, previous_events)
        clusters = self.score(clusters)
        selected = self.diversify(clusters, max_posts=max_posts)
        for c in selected:
            c["representative"] = choose_representative(c)
        return selected, clusters
