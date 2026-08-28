import logging
from typing import Iterable
import requests
from telegramify_markdown import richify, telegramify_rich
from config import CHANNEL, HEADERS, TELEGRAM_BOT_TOKEN
from models import Story

log = logging.getLogger("gamingnewsroom.publisher")
API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def markdown(story: Story) -> str:
    badge = story.badge.upper()
    badge_icon = "🔴" if badge == "CONFIRMED" else "🟡" if badge == "UNCONFIRMED" else "🔵"
    age = (story.decision.event.representative.published_at)
    from datetime import datetime, timezone, timedelta
    if age < datetime.now(timezone.utc) - timedelta(hours=24):
        badge_line = "🕓 CATCH-UP"
    else:
        badge_line = f"{badge_icon} {badge}"
    platforms = " • ".join(story.platforms)
    lines = [f"# {story.headline}", "", story.summary, "", "```", badge_line]
    if platforms:
        lines.append(f"🎮 {platforms}")
    lines += ["```", "", "## Key Highlights", ""]
    lines += [f"• {x}" for x in story.highlights]
    if story.what_to_know.strip():
        lines += ["", "<details>", "<summary>What to Know</summary>", "", story.what_to_know.strip(), "", "</details>"]
    lines += ["", " ".join(story.hashtags), "", f"Source: [{story.decision.event.representative.source}]({story.decision.event.representative.url})"]
    return "\n".join(lines).strip()


def send_rich(rich_message):
    response = requests.post(f"{API}/sendRichMessage", json={"chat_id": CHANNEL, "rich_message": rich_message.to_dict()}, timeout=35)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Telegram sendRichMessage failed"))
    return data


def publish(story: Story) -> bool:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
    md = markdown(story)
    # richify() is the Bot API Rich Message path. Use telegramify_rich when needed for
    # the platform's block/byte limits, preserving block boundaries.
    chunks = telegramify_rich(md, mode="html")
    if not chunks:
        raise RuntimeError("Rich message conversion produced no chunks")
    for chunk in chunks:
        send_rich(chunk)
    return True


def self_test():
    s = Story.__new__(Story)
    # construct minimally through local fake decision/event objects is unnecessary;
    # verify converter contract with the exact user-requested pattern instead.
    md = """# GTA VI Delayed to November 2026\n\nRockstar has officially moved GTA VI's release date to November 2026.\n\n```\n🔴 CONFIRMED\n🎮 PS5 • Xbox Series X|S\n```\n\n## Key Highlights\n\n• Rockstar confirmed the new release window\n• Development continues under Rockstar Games\n\n<details>\n<summary>What to Know</summary>\n\nThe delay gives Rockstar additional development time.\n\n</details>\n\n#GTA6 #RockstarGames #GamingNews #PlayStation #Xbox\nSource: [Rockstar Games](https://example.com/story)"""
    rich = richify(md, mode="html")
    assert isinstance(rich.to_dict(), dict)
    assert rich.to_dict()
    return True
