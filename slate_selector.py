from event_engine import EventEngine

def select_slate(engine: EventEngine, clusters: list[dict], max_posts: int = 20) -> list[dict]:
    return engine.diversify(clusters, max_posts=max_posts)
