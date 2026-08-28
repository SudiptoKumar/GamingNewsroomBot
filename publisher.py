import html
import logging
import re
from pathlib import Path
import requests

from config import CHANNEL, HEADERS, MAX_ARTICLE_CHARS, TELEGRAM_CAPTION_LIMIT, TOKEN
from models import Story

log = logging.getLogger("gamingnewsroom.publisher")

def fetch_article(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=22)
        r.raise_for_status()
        text = r.text
        image = ""
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', text, re.I)
        if not m:
            m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', text, re.I)
        if m:
            image = m.group(1)
        # Lightweight HTML-to-text extraction. No browser or heavy extractor required.
        body = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>", " ", text)
        body = re.sub(r"(?s)<[^>]+>", " ", body)
        body = re.sub(r"\s+", " ", html.unescape(body)).strip()
        return body[:MAX_ARTICLE_CHARS], image
    except Exception as exc:
        log.warning("ARTICLE FETCH FAILED %s: %s", url, exc)
        return "", ""

def esc(s):
    return html.escape(str(s or ""), quote=False)

def caption(story: Story):
    badge = "Catch-up" if story.decision.event.representative.published_at and story.decision.event.representative.published_at < __import__("datetime").datetime.now(__import__("datetime").timezone.utc) - __import__("datetime").timedelta(hours=24) else story.badge
    parts = [
        f"<b>{esc(story.headline)}</b>",
        esc(f"{badge} • {' • '.join(story.platforms) if story.platforms else 'Gaming'}"),
        esc(story.summary[:220]),
    ]
    for item in story.highlights[:2]:
        parts.append("• " + esc(item[:150]))
    if story.what_to_know:
        parts.append("<b>What to Know</b>")
        for row in story.what_to_know[:2]:
            parts.append(f"<b>{esc(row.get('term'))}</b>: {esc(row.get('meaning'))}")
    parts.append(" ".join(esc(x) for x in story.hashtags[:5]))
    parts.append(f"Source: {esc(story.decision.event.representative.source)}")
    parts.append(f'<a href="{html.escape(story.decision.event.representative.url, quote=True)}">Original article</a>')
    result = "\n".join(x for x in parts if x.strip())
    return result[:TELEGRAM_CAPTION_LIMIT]

def telegram(method, data=None, files=None):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    r = requests.post(url, data=data or {}, files=files, timeout=30)
    payload = r.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description", "Telegram API failed"))
    return payload

def publish(story: Story):
    text = caption(story)
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
    try:
        if story.image_url:
            r = requests.get(story.image_url, headers=HEADERS, timeout=12)
            if r.ok and "image" in r.headers.get("Content-Type", ""):
                path = Path("/tmp/gamingnewsroom.jpg")
                path.write_bytes(r.content)
                try:
                    with path.open("rb") as fh:
                        telegram("sendPhoto", {"chat_id": CHANNEL, "caption": text, "parse_mode": "HTML"}, {"photo": fh})
                finally:
                    path.unlink(missing_ok=True)
                return True
        telegram("sendMessage", {"chat_id": CHANNEL, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False})
        return True
    except Exception as exc:
        log.error("TELEGRAM PUBLISH FAILED: %s", exc)
        return False
