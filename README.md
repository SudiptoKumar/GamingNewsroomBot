# GamingNewsroom V1

GamingNewsroom is a daily automated Telegram gaming-newsroom built around a gaming-specific editorial pipeline.

## Pipeline

DISCOVER -> NORMALIZE -> DEDUPE -> EVENT CLUSTER -> SCORE -> ARTICLE EXTRACT -> GENERATE -> TELEGRAM RICH MESSAGE -> PERSIST

Primary lookback is 72 hours, with a 0-24h primary bucket and 24-72h catch-up bucket. Stories publish when the editorial score is >= 7/10. There is no fixed daily quota. A thin primary day (<3 publishable events) opens the fallback source set and applies the same threshold.

## Telegram Rich Messages

The publisher uses Telegram Bot API `sendRichMessage` and `telegramify-markdown` `richify()` with HTML mode. This is the structured Rich Message API path, not Telethon and not `parse_mode=HTML`.

The target story structure is:

```markdown
# Headline

One-line summary.

```
🔴 CONFIRMED
🎮 PS5 • Xbox Series X|S
```

## Key Highlights

• Highlight
• Highlight

<details>
<summary>What to Know</summary>

Short context paragraph.

</details>

#GTA6 #GamingNews
Source: [Publisher](https://example.com/article)
```

`richify()` converts this into the Bot API `InputRichMessage` payload used by `sendRichMessage`. Rich Messages were introduced in Telegram Bot API 10.1, and Bot API 10.2 added explicit embedded media support. The converter also supports automatic Rich Message splitting through `telegramify_rich()`.

## Secrets

- `EXA_API_KEY`
- `CEREBRAS_API_KEY`
- `TELEGRAM_BOT_TOKEN`

No Telegram API ID/hash and no Telethon account session are required.

## State

- `gaming_state.json`
- `posted_urls.txt`

Do not overwrite these during code deployment.
