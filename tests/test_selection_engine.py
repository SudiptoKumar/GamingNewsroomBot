import json
from datetime import datetime, timezone

from selection_engine import SelectionEngine


class FakeResponse:
    class Choice:
        class Message:
            content = json.dumps({"ranked": []})
        message = Message()
    choices = [Choice()]


def engine(state=None, score_threshold=80, max_posts=20, ai_create=None):
    state = {} if state is None else state
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def parse_dt(value):
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    return SelectionEngine(
        state=state,
        now_provider=lambda: now,
        ai_create=ai_create or (lambda **kwargs: FakeResponse()),
        canonical_topic=lambda value, region: value or "Game Updates",
        title_similarity=lambda a, b: 1.0 if a == b else 0.0,
        event_similarity=lambda a, b: 1.0 if a.get("title") == b.get("title") else 0.0,
        same_event_window=lambda a, b, hours=36: True,
        normalize_title=lambda value: str(value).lower().strip(),
        safe_text=lambda value: "" if value is None else str(value),
        trim_source_text=lambda value, limit: str(value)[:limit],
        parse_datetime=parse_dt,
        persist_event_cluster_state=lambda rows: None,
        topics=["Game Updates", "Major Releases"],
        publish_score_threshold=score_threshold,
        max_posts_per_run=max_posts,
    )


def ranked(scores):
    return [
        {
            "importance_score": score,
            "important": score >= 80,
            "event_source_count": 1,
            "editor_rank": index + 1,
        }
        for index, score in enumerate(scores)
    ]


def test_dynamic_volume_and_safety_ceiling():
    sel = engine()
    assert len(sel.select_publishable(ranked([100] * 20))) == 20
    assert len(sel.select_publishable(ranked([100] * 10))) == 10
    assert len(sel.select_publishable(ranked([100] * 5))) == 5
    assert len(sel.select_publishable(ranked([100]))) == 1
    assert len(sel.select_publishable(ranked([]))) == 0
    assert len(sel.select_publishable(ranked([100] * 25))) == 20


def test_threshold_boundary():
    sel = engine()
    assert len(sel.select_publishable(ranked([79]))) == 0
    assert len(sel.select_publishable(ranked([80]))) == 1


def test_ranking_order_is_score_first_then_confirmation():
    sel = engine()
    rows = [
        {"importance_score": 85, "important": True, "event_source_count": 1, "editor_rank": 2},
        {"importance_score": 90, "important": True, "event_source_count": 1, "editor_rank": 3},
        {"importance_score": 85, "important": True, "event_source_count": 3, "editor_rank": 4},
    ]
    selected = sel.select_publishable(rows)
    assert [x["importance_score"] for x in selected] == [90, 85, 85]
    assert selected[1]["event_source_count"] == 3


def test_precluster_collapses_same_event():
    sel = engine()
    candidates = [
        {"title": "Game X launches", "source": "IGN", "canonical": "ign/x", "published_date": "2026-01-01T01:00:00+00:00", "excerpt": "A"},
        {"title": "Game X launches", "source": "VGC", "canonical": "vgc/x", "published_date": "2026-01-01T00:30:00+00:00", "excerpt": "B"},
    ]
    clusters = sel.precluster_candidates(candidates)
    assert len(clusters) == 1
    assert clusters[0]["source_count"] == 2
