from __future__ import annotations
import re

def validate_story(story: dict) -> tuple[bool, list[str]]:
    errors = []
    headline = str(story.get("headline") or "").strip()
    summary = str(story.get("summary") or "").strip()
    highlights = [str(x or "").strip() for x in story.get("highlights", [])]
    if not headline: errors.append("missing_headline")
    if not summary: errors.append("missing_summary")
    if not (3 <= len(highlights) <= 5): errors.append("highlights_count")
    if "*" in " ".join([headline, summary, *highlights, str(story.get("why_it_matters") or ""), str(story.get("whats_next") or "")]):
        errors.append("markdown_asterisk")
    if re.search(r"[,:;\-—…]\s*$", headline + summary): errors.append("incomplete_text")
    if story.get("importance_score", 0) < 80: errors.append("below_threshold")
    return not errors, errors
