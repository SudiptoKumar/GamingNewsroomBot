from __future__ import annotations
from datetime import datetime, timedelta, timezone

def ensure_v2_state(state: dict) -> dict:
    state.setdefault("version", "V2")
    state.setdefault("events", {})
    state.setdefault("event_clusters", {})
    state.setdefault("posted_event_ids", [])
    state.setdefault("recent_titles", [])
    return state

def upsert_event(state: dict, story: dict, published: bool, message_id=None) -> str:
    ensure_v2_state(state)
    event_id = story.get("event_cluster_id") or story.get("event_id") or story.get("event_key") or story.get("canonical")
    event_id = str(event_id)
    prior = state["events"].get(event_id, {})
    event = dict(prior)
    event.update({
        "event_id": event_id,
        "event_key": story.get("event_key", prior.get("event_key", "")),
        "event_subject": story.get("event_subject", prior.get("event_subject", "")),
        "event_type": story.get("event_type", prior.get("event_type", "")),
        "event_frame": story.get("event_frame", prior.get("event_frame", {})),
        "sources": story.get("event_sources", prior.get("sources", [])),
        "event_sources": story.get("event_sources", prior.get("event_sources", [])),
        "claims": story.get("claims", prior.get("claims", [])),
        "importance_score": story.get("importance_score", prior.get("importance_score", 0)),
        "coverage_score": story.get("coverage_score", prior.get("coverage_score", 0)),
        "originality_score": story.get("originality_score", prior.get("originality_score", 0)),
        "significance_score": story.get("significance_score", prior.get("significance_score", 0)),
        "freshness_score": story.get("freshness_score", prior.get("freshness_score", 0)),
        "source_trust_score": story.get("source_trust_score", prior.get("source_trust_score", 0)),
        "headline": story.get("headline", prior.get("headline", "")),
        "summary": story.get("summary", prior.get("summary", "")),
        "published_at": story.get("published_date") or prior.get("published_at"),
        "selected_at": datetime.now(timezone.utc).isoformat(),
        "status": "published" if published else prior.get("status", "selected"),
        "message_id": message_id if message_id is not None else prior.get("message_id"),
    })
    versions = list(prior.get("published_versions", []))
    if published:
        versions.append({"published_at": event["selected_at"], "headline": event["headline"], "claims": event["claims"]})
    event["published_versions"] = versions[-10:]
    state["events"][event_id] = event
    return event_id

def prune_event_store(state: dict, days: int = 45):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    keep = {}
    for key, event in state.get("events", {}).items():
        raw = event.get("selected_at") or event.get("published_at")
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            dt = cutoff
        if dt >= cutoff:
            keep[key] = event
    state["events"] = keep
