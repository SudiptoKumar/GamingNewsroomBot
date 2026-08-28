from dataclasses import dataclass, field
from datetime import datetime

@dataclass(slots=True)
class Candidate:
    title: str
    url: str
    source: str
    domain: str
    published_at: datetime
    summary: str = ""
    image_url: str = ""
    discovery: str = "rss"
    tier: int = 2

@dataclass(slots=True)
class Event:
    key: str
    representative: Candidate
    coverage: list[Candidate] = field(default_factory=list)

@dataclass(slots=True)
class Decision:
    event: Event
    score: float
    important: bool
    exclusive: bool
    confidence: str
    trending: bool
    category: str
    reason: str

@dataclass(slots=True)
class Story:
    decision: Decision
    headline: str
    summary: str
    highlights: list[str]
    what_to_know: str
    platforms: list[str]
    hashtags: list[str]
    badge: str
    image_url: str
