# GamingNewsroom V1

A gaming-specific Telegram news publisher built around the GamingNewsroom editorial contract.

Pipeline: 20-source discovery → 72h normalization → event deduplication → Cerebras importance classification → score >= 7 → story generation → Telegram Bot API `sendRichMessage` via `telegramify-markdown` → persistent state.

Required GitHub Secrets:
- `EXA_API_KEY`
- `CEREBRAS_API_KEY`
- `TELEGRAM_BOT_TOKEN`

Optional:
- `TELEGRAM_CHANNEL` (default `@GamingNewsroom`)
- `CEREBRAS_MODEL` (default `gpt-oss-120b`)

No Telethon. No Telegram API ID/hash.
