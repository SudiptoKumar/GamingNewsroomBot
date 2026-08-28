from dataclasses import dataclass
from datetime import datetime
from typing import Any

@dataclass
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

@dataclass
class Event:
    key: str
    representative: Candidate
    coverage: list[Candidate]

@dataclass
class Decision:
    event: Event
    score: float
    important: bool
    exclusive: bool
    confidence: str
    trending: bool
    category: str
    reason: str

@dataclass
class Story:
    decision: Decision
    headline: str
    summary: str
    highlights: list[str]
    what_to_know: list[dict[str, str]]
    platforms: list[str]
    hashtags: list[str]
    badge: str
    article_text: str
    image_url: str
