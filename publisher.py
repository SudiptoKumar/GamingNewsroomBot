"""Telegram Bot API publisher with HTML rich text."""
from __future__ import annotations
import html
import logging
import time
from typing import Optional
import requests

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"

def _call(token: str, method: str, payload: dict) -> dict:
    r = requests.post(API.format(token=token, method=method), json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Telegram API error"))
    return data

def _esc(s: str) -> str:
    return html.escape(str(s or ""), quote=False)

def build_html(story: dict) -> str:
    title = _esc(story.get("headline") or story.get("title") or "Gaming News")
    summary = _esc(story.get("summary") or "")
    status = _esc(story.get("status") or story.get("confidence") or "CONFIRMED").upper()
    platforms = story.get("platforms") or story.get("platform_tags") or []
    if isinstance(platforms, str):
        platforms = [platforms]
    platform_line = "🎮 " + " • ".join(_esc(x) for x in platforms) if platforms else ""
    highlights = story.get("highlights") or story.get("key_highlights") or []
    if isinstance(highlights, str):
        highlights = [highlights]
    know = story.get("what_to_know") or story.get("what_to_know_text") or ""
    hashtags = story.get("hashtags") or []
    if isinstance(hashtags, str):
        hashtags = [hashtags]
    source = _esc(story.get("source") or story.get("source_name") or "Source")
    url = story.get("url") or story.get("original_url") or ""
    lines = [f"<b>{title}</b>", "", summary, "", "<pre>"]
    lines.append(("🔴 " if status == "CONFIRMED" else "🟡 ") + status)
    if platform_line:
        lines.append(platform_line)
    lines.append("</pre>", "", "<b>Key Highlights</b>", "")
    lines.extend("• " + _esc(h) for h in highlights[:6])
    if know:
        lines += ["", "<blockquote expandable><b>What to Know</b>", "", _esc(know), "</blockquote>"]
    if hashtags:
        lines += ["", " ".join(_esc(h) for h in hashtags)]
    if url:
        lines += ["", f'Source: <a href="{html.escape(url, quote=True)}">{source}</a>']
    else:
        lines += ["", f"Source: {source}"]
    return "\n".join(lines).strip()

def publish_story(token: str, chat_id: str, story: dict, image_url: Optional[str] = None) -> bool:
    caption = build_html(story)
    if image_url:
        payload = {"chat_id": chat_id, "photo": image_url, "caption": caption, "parse_mode": "HTML"}
        _call(token, "sendPhoto", payload)
    else:
        payload = {"chat_id": chat_id, "text": caption, "parse_mode": "HTML", "disable_web_page_preview": False}
        _call(token, "sendMessage", payload)
    return True

def publish_stories(token: str, chat_id: str, stories: list[dict], delay: float = 1.0) -> int:
    published = 0
    for story in stories:
        try:
            publish_story(token, chat_id, story, story.get("image_url"))
            published += 1
        except Exception as exc:
            log.error("TELEGRAM PUBLISH FAILED: %s", exc)
        if delay:
            time.sleep(delay)
    log.info("TELEGRAM PUBLISHED=%d/%d", published, len(stories))
    return published

def self_test() -> None:
    sample = {
        "headline":"GTA VI Delayed to November 2026",
        "summary":"Rockstar has officially moved GTA VI's release date to November 2026.",
        "status":"confirmed",
        "platforms":["PS5","Xbox Series X|S"],
        "highlights":["Rockstar confirmed the new release window","Development continues under Rockstar Games"],
        "what_to_know":"The delay gives Rockstar additional development time.",
        "hashtags":["#GTA6","#RockstarGames","#GamingNews"],
        "source":"Rockstar Games","url":"https://example.com/story"
    }
    out=build_html(sample)
    assert "<pre>" in out and "</pre>" in out
    assert "<blockquote expandable>" in out
    assert '<a href="https://example.com/story">Rockstar Games</a>' in out
