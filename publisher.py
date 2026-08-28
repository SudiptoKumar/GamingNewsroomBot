import asyncio
import html
import logging
import re
import tempfile
from pathlib import Path

import requests
from config import (
    CHANNEL,
    HEADERS,
    MAX_ARTICLE_CHARS,
    TELEGRAM_API_HASH,
    TELEGRAM_API_ID,
    TELEGRAM_CAPTION_LIMIT,
    TOKEN,
)
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
            image = html.unescape(m.group(1))
        body = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>", " ", text)
        body = re.sub(r"(?s)<[^>]+>", " ", body)
        body = re.sub(r"\s+", " ", html.unescape(body)).strip()
        return body[:MAX_ARTICLE_CHARS], image
    except Exception as exc:
        log.warning("ARTICLE FETCH FAILED %s: %s", url, exc)
        return "", ""


def clean_text(value):
    value = str(value or "").replace("\x00", "")
    return re.sub(r"\s+", " ", value).strip()


def html_text(value):
    return html.escape(clean_text(value), quote=True)


def build_rich_html(story: Story):
    # Telethon's HTML mode is used because v2 explicitly supports <details>
    # spoilers as well as <pre> blocks and <a> links.
    # See: https://docs.telethon.dev/en/v2/concepts/messages.html
    title = html_text(story.headline)
    summary = html_text(story.summary)
    badge = clean_text(story.badge or ("Unconfirmed" if story.decision.confidence != "confirmed" else "Confirmed"))
    platforms = story.platforms[:5] or ["Gaming"]
    if story.decision.event.representative.published_at:
        age_hours = (
            __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
            - story.decision.event.representative.published_at
        ).total_seconds() / 3600
        if age_hours > 24:
            badge = "🕓 Catch-up"

    block = f"{badge}\n🎮 {' • '.join(clean_text(p) for p in platforms)}"
    lines = [f"<b>{title}</b>", "", summary, "", f"<pre>{html.escape(block, quote=False)}</pre>", ""]

    lines.append("<b>Key Highlights</b>")
    for item in story.highlights[:4]:
        lines.append("• " + html_text(item))

    if story.what_to_know:
        lines.extend([
            "",
            "<details>",
            "<summary>What to Know</summary>",
            "",
        ])
        for row in story.what_to_know[:4]:
            term = html_text(row.get("term", ""))
            meaning = html_text(row.get("meaning", ""))
            if term:
                lines.append(f"<b>{term}</b>: {meaning}")
            elif meaning:
                lines.append(meaning)
        lines.extend(["", "</details>"])

    tags = " ".join(clean_text(x) for x in story.hashtags[:8] if clean_text(x))
    if tags:
        lines.extend(["", html_text(tags)])

    source = html_text(story.decision.event.representative.source)
    source_url = story.decision.event.representative.url
    # The URL stays hidden behind the source name, matching the reference layout.
    lines.extend(["", f'Source: <a href="{html.escape(source_url, quote=True)}">{source}</a>'])

    result = "\n".join(lines)
    # Telegram photo captions are limited to 1024 characters. Keep the structure
    # intact, trimming only optional lower-priority sections when necessary.
    if len(result) <= TELEGRAM_CAPTION_LIMIT:
        return result

    compact = [f"<b>{title}</b>", "", summary, "", f"<pre>{html.escape(block, quote=False)}</pre>", "", "<b>Key Highlights</b>"]
    for item in story.highlights[:3]:
        compact.append("• " + html_text(item))
    if story.what_to_know:
        compact.extend(["", "<details>", "<summary>What to Know</summary>", ""])
        row = story.what_to_know[0]
        compact.append(f"<b>{html_text(row.get('term',''))}</b>: {html_text(row.get('meaning',''))}")
        compact.extend(["", "</details>"])
    if tags:
        compact.extend(["", html_text(tags)])
    compact.extend(["", f'Source: <a href="{html.escape(source_url, quote=True)}">{source}</a>'])
    result = "\n".join(compact)
    return result[:TELEGRAM_CAPTION_LIMIT]


def _ensure_telethon_credentials():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
    if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
        raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required for Telethon")


async def _publish_async(story: Story):
    from telethon import Client

    _ensure_telethon_credentials()
    message = build_rich_html(story)

    # A memory session avoids committing a Telegram session file to GitHub.
    async with Client(None, TELEGRAM_API_ID, TELEGRAM_API_HASH) as client:
        if not await client.is_authorized():
            await client.bot_sign_in(TOKEN)

        if story.image_url:
            try:
                response = requests.get(story.image_url, headers=HEADERS, timeout=15)
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "")
                if "image" not in content_type.lower():
                    raise RuntimeError(f"image URL returned {content_type or 'unknown content type'}")
                with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                    tmp.write(response.content)
                    image_path = Path(tmp.name)
                try:
                    await client.send_photo(
                        CHANNEL,
                        image_path,
                        caption_html=message,
                    )
                    return True
                finally:
                    image_path.unlink(missing_ok=True)
            except Exception as exc:
                log.warning("TELETHON PHOTO SEND FAILED, falling back to text: %s", exc)

        await client.send_message(CHANNEL, html=message, link_preview=False)
        return True


def publish(story: Story):
    try:
        return asyncio.run(_publish_async(story))
    except Exception as exc:
        log.error("TELETHON PUBLISH FAILED: %s", exc)
        return False
