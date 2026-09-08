from event_engine import source_tier

def explain_score(event: dict) -> str:
    return (
        f"significance={event.get('significance_score',0)}/45, "
        f"coverage={event.get('coverage_score',0)}/20, "
        f"originality={event.get('originality_score',0)}/15, "
        f"freshness={event.get('freshness_score',0)}/10, "
        f"trust={event.get('source_trust_score',0)}/10"
    )

def score_breakdown(event: dict) -> dict:
    return {
        "significance": int(event.get("significance_score", 0)),
        "coverage": int(event.get("coverage_score", 0)),
        "originality": int(event.get("originality_score", 0)),
        "freshness": int(event.get("freshness_score", 0)),
        "source_trust": int(event.get("source_trust_score", 0)),
        "total": int(event.get("importance_score", 0)),
    }
