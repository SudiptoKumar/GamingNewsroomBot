# Multi-API Cerebras Setup

GamingNewsroom can use 1 to 10 Cerebras API keys for the same model. Keys are supplied only through GitHub Actions Secrets. No API key value is stored in Python, YAML, JSON, logs, persistent state, tests, or release archives.

## Required GitHub Secrets

Keep the existing secrets:

```text
EXA_API_KEY
TELEGRAM_BOT_TOKEN
```

Configure any subset of:

```text
CEREBRAS_API_KEY_1
CEREBRAS_API_KEY_2
CEREBRAS_API_KEY_3
CEREBRAS_API_KEY_4
CEREBRAS_API_KEY_5
CEREBRAS_API_KEY_6
CEREBRAS_API_KEY_7
CEREBRAS_API_KEY_8
CEREBRAS_API_KEY_9
CEREBRAS_API_KEY_10
```

`CEREBRAS_API_KEY` is also supported for backward compatibility and is treated as slot 1 when `CEREBRAS_API_KEY_1` is not set.

## Two-key configuration

Set only:

```text
CEREBRAS_API_KEY_1
CEREBRAS_API_KEY_2
```

The bot uses the last successful API first. If that API temporarily fails, the other configured API is tried automatically.

## Three-key configuration

Set:

```text
CEREBRAS_API_KEY_1
CEREBRAS_API_KEY_2
CEREBRAS_API_KEY_3
```

No code change is required.

## Slots 1-10

You can configure any subset through slot 10. Empty slots are ignored. Non-contiguous configurations are supported, for example:

```text
API 1: configured
API 2: empty
API 3: configured
API 4: empty
API 5: configured
```

The router will use only configured and currently eligible slots.

## Preferred API

`news_state.json` stores non-secret metadata under `ai_router`:

```json
{
  "preferred_api_index": 1,
  "api_status": {}
}
```

The index is zero-based, so `1` means API 2. If a request succeeds on API 2, API 2 becomes the preferred API for the next workflow run.

No API-key value is stored in state.

## Failover

For a successful request:

```text
Preferred API 2
      ↓
API 2 succeeds
      ↓
Keep API 2 preferred
```

For a temporary provider failure:

```text
Preferred API 2
      ↓
API 2 → HTTP 429
      ↓
API 2 cooldown
      ↓
API 3
      ↓
API 3 succeeds
      ↓
API 3 becomes preferred
```

The router tries the preferred API first, followed by the next eligible configured slots in deterministic order and then wraps around to lower-numbered slots.

## What triggers failover

Failover is used for:

- HTTP 429 / rate limit / quota exhaustion
- HTTP 500, 502, 503, 504
- timeouts
- connection failures
- temporary provider capacity/unavailability

Authentication failures such as HTTP 401/403 are isolated to the affected slot for the current run. Permanent request errors such as malformed/invalid parameters or unsupported models are not hidden by rotating to another key.

## Cooldown and recovery

A temporary failure places the affected API in cooldown. If the provider supplies `Retry-After`, that value is respected. Otherwise the router applies a bounded default cooldown.

The router does not repeatedly test a failed API during cooldown. After the cooldown expires, the API becomes eligible again automatically.

A previously authentication-failed slot is rechecked on a later workflow run so a corrected GitHub Secret can recover without code changes.

## All APIs fail

If every eligible configured API fails temporarily, the current AI operation fails gracefully. The news pipeline does not recollect news or create a duplicate logical operation just because AI failover was exhausted.

If all configured APIs are unavailable because of cooldown, the router fails cleanly rather than making unnecessary health-check calls.

## Adding more keys later

No architecture change is required. For example, start with:

```text
CEREBRAS_API_KEY_1
CEREBRAS_API_KEY_2
```

Then later add:

```text
CEREBRAS_API_KEY_3
CEREBRAS_API_KEY_4
```

The next workflow automatically discovers the new keys.

## GitHub Actions logs

The workflow reports safe metadata such as:

```text
API 1: configured
API 2: configured
API 3: not configured
Preferred API: API 2
Trying API 2...
API 2 succeeded.
```

On failover:

```text
Trying API 2...
API 2 temporarily unavailable...
Trying API 4...
API 4 succeeded. New preferred API: API 4
```

API values, authorization headers, and credentials are never logged.

## State persistence

The existing `news_state.json` remains the single persistent state file. The workflow already commits state changes after each run. The AI router writes only non-secret routing metadata into the existing state object.

The workflow uses concurrency protection so scheduled/manual executions in the same workflow group do not run concurrently and race on state.

## Local tests

Run:

```bash
pytest -q
```

The AI router tests use mocked clients and do not contact Cerebras.

## Git safety

Never paste real API keys into:

- Python source
- workflow YAML
- JSON state
- README/docs
- tests
- logs
- Git history
- ZIP archives

Use GitHub Actions Secrets only.
