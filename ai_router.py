"""Production-grade Cerebras API-key failover router.

The router supports 1-10 keys for the same provider/model, persists only
non-secret routing metadata in the application's existing state object, and
fails over only for errors that are reasonably retryable at the provider/API
layer.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_KEYS = 10
DEFAULT_COOLDOWN_SECONDS = 90
DEFAULT_PROVIDER_COOLDOWN_SECONDS = 120
MAX_COOLDOWN_SECONDS = 3600

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
AUTH_STATUS_CODES = {401, 403}
PERMANENT_STATUS_CODES = {400, 404, 409, 422}


class AIRouterError(RuntimeError):
    """Base router exception."""


class AIRequestError(AIRouterError):
    """Raised when no eligible API can complete an AI request."""

    def __init__(self, message: str, *, last_error: Optional[BaseException] = None):
        super().__init__(message)
        self.last_error = last_error


class AIPermanentRequestError(AIRouterError):
    """Raised for a request/programming error that should not rotate keys."""


def _safe_status_code(exc: BaseException) -> Optional[int]:
    for attr in ("status_code", "http_status", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int):
            return value
    return None


def _headers_from_exception(exc: BaseException) -> Dict[str, str]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if headers is None:
        headers = getattr(exc, "headers", None)
    if not headers:
        return {}
    try:
        return {str(k).lower(): str(v) for k, v in headers.items()}
    except Exception:
        return {}


def _retry_after_seconds(exc: BaseException) -> Optional[float]:
    value = _headers_from_exception(exc).get("retry-after")
    if value is not None:
        try:
            seconds = float(value.strip())
            if seconds >= 0:
                return min(seconds, MAX_COOLDOWN_SECONDS)
        except (TypeError, ValueError):
            pass
    return None


def classify_failure(exc: BaseException) -> str:
    """Return one of: temporary, auth, permanent, unknown."""
    status = _safe_status_code(exc)
    if status in RETRYABLE_STATUS_CODES:
        return "temporary"
    if status in AUTH_STATUS_CODES:
        return "auth"
    if status in PERMANENT_STATUS_CODES:
        return "permanent"

    text = str(exc).lower()
    if any(term in text for term in ("rate limit", "rate_limit", "quota", "too many requests")):
        return "temporary"
    if any(term in text for term in ("timeout", "timed out", "connection reset", "connection refused", "connection error", "temporarily unavailable", "service unavailable", "gateway")):
        return "temporary"
    if any(term in text for term in ("unauthorized", "authentication", "invalid api key", "invalid_api_key", "forbidden", "revoked")):
        return "auth"
    if any(term in text for term in ("invalid parameter", "invalid request", "unsupported model", "malformed request", "validation error")):
        return "permanent"
    return "unknown"


class AIRouter:
    """Route Cerebras requests through configured keys with persistent failover."""

    def __init__(
        self,
        model: str,
        state: Dict[str, Any],
        save_state: Callable[[Dict[str, Any]], None],
        *,
        env: Optional[Dict[str, str]] = None,
        max_keys: int = MAX_KEYS,
        client_factory: Optional[Callable[[str], Any]] = None,
        time_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.model = model
        self.state = state
        self.save_state = save_state
        self.env = env if env is not None else os.environ
        self.max_keys = max(1, min(MAX_KEYS, int(max_keys)))
        self.client_factory = client_factory or self._default_client_factory
        self.time_fn = time_fn
        self.sleep_fn = sleep_fn
        self._clients: Dict[int, Any] = {}
        self._configured: Dict[int, str] = self._discover_keys()
        self._router_state = self._ensure_router_state()
        self._clear_stale_auth_disables()
        self._log_configuration()

    @staticmethod
    def _default_client_factory(api_key: str) -> Any:
        from cerebras.cloud.sdk import Cerebras
        return Cerebras(api_key=api_key)

    def _discover_keys(self) -> Dict[int, str]:
        keys: Dict[int, str] = {}
        for index in range(1, self.max_keys + 1):
            value = (self.env.get(f"CEREBRAS_API_KEY_{index}") or "").strip()
            # Backward compatibility: an existing CEREBRAS_API_KEY is slot 1.
            if index == 1 and not value:
                value = (self.env.get("CEREBRAS_API_KEY") or "").strip()
            if value:
                keys[index - 1] = value
        return keys

    def _ensure_router_state(self) -> Dict[str, Any]:
        state = self.state.setdefault("ai_router", {})
        if not isinstance(state, dict):
            state = {}
            self.state["ai_router"] = state
        preferred = state.get("preferred_api_index")
        if (not isinstance(preferred, int) or not (0 <= preferred < self.max_keys)
                or (self._configured and preferred not in self._configured)):
            preferred = min(self._configured) if self._configured else 0
            state["preferred_api_index"] = preferred

        statuses = state.get("api_status")
        if not isinstance(statuses, dict):
            statuses = {}
            state["api_status"] = statuses
        return state

    def _clear_stale_auth_disables(self) -> None:
        # Authentication failures may reflect a changed GitHub secret on the
        # next workflow run. Do not permanently lock a slot out based on an
        # old secret value. Temporary cooldown state remains persistent.
        changed = False
        statuses = self._router_state.setdefault("api_status", {})
        for key in list(statuses):
            entry = statuses[key]
            if isinstance(entry, dict) and entry.get("status") == "auth_invalid":
                entry.clear()
                changed = True
        if changed:
            self.save_state(self.state)

    def _log_configuration(self) -> None:
        for index in range(self.max_keys):
            label = f"API {index + 1}"
            logger.info("%s: %s", label, "configured" if index in self._configured else "not configured")
        preferred = self._router_state.get("preferred_api_index", 0) + 1
        logger.info("Preferred API: API %d", preferred)

    def _status_entry(self, index: int) -> Dict[str, Any]:
        statuses = self._router_state.setdefault("api_status", {})
        key = str(index)
        entry = statuses.get(key)
        if not isinstance(entry, dict):
            entry = {}
            statuses[key] = entry
        return entry

    def _cooldown_until(self, index: int) -> float:
        value = self._status_entry(index).get("retry_after")
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _eligible(self, index: int) -> bool:
        if index not in self._configured:
            return False
        entry = self._status_entry(index)
        if entry.get("status") == "auth_invalid":
            return False
        return self._cooldown_until(index) <= self.time_fn()

    def _ordered_candidates(self) -> List[int]:
        if not self._configured:
            return []
        preferred = int(self._router_state.get("preferred_api_index", 0))
        order = list(range(preferred, self.max_keys)) + list(range(0, preferred))
        return [index for index in order if self._eligible(index)]

    def _client(self, index: int) -> Any:
        if index not in self._clients:
            self._clients[index] = self.client_factory(self._configured[index])
        return self._clients[index]

    def _mark_success(self, index: int) -> None:
        entry = self._status_entry(index)
        entry.clear()
        entry["status"] = "active"
        entry["last_success_at"] = self.time_fn()
        self._router_state["preferred_api_index"] = index
        self._router_state["last_success_api_index"] = index
        self._router_state["last_success_at"] = self.time_fn()
        self.save_state(self.state)
        logger.info("API %d succeeded. New preferred API: API %d", index + 1, index + 1)

    def _mark_temporary_failure(self, index: int, exc: BaseException) -> None:
        status = _safe_status_code(exc)
        retry_after = _retry_after_seconds(exc)
        if retry_after is None:
            retry_after = DEFAULT_COOLDOWN_SECONDS if status == 429 else DEFAULT_PROVIDER_COOLDOWN_SECONDS
        retry_at = self.time_fn() + min(float(retry_after), MAX_COOLDOWN_SECONDS)
        entry = self._status_entry(index)
        entry.update({
            "status": "cooldown",
            "retry_after": retry_at,
            "last_error_type": "http_%s" % status if status else "temporary",
            "last_error_at": self.time_fn(),
        })
        self.save_state(self.state)
        logger.warning(
            "API %d temporarily unavailable (status=%s); cooldown %.0fs.",
            index + 1,
            status or "unknown",
            max(0.0, retry_at - self.time_fn()),
        )

    def _mark_auth_failure(self, index: int) -> None:
        entry = self._status_entry(index)
        entry.update({"status": "auth_invalid", "last_error_at": self.time_fn()})
        self.save_state(self.state)
        logger.error("API %d authentication failed; skipping this slot for the current run.", index + 1)

    def create(self, **kwargs: Any) -> Any:
        """Execute one Cerebras request, failing over only when appropriate."""
        if not self._configured:
            raise AIRequestError("No configured Cerebras API keys found.")

        if "model" in kwargs and kwargs["model"] != self.model:
            raise AIPermanentRequestError(
                f"Router configured for model {self.model!r}, got {kwargs['model']!r}"
            )
        kwargs = dict(kwargs)
        kwargs["model"] = self.model

        candidates = self._ordered_candidates()
        if not candidates:
            # A cooldown may cover every configured API. Do not hammer them.
            raise AIRequestError("No eligible Cerebras API key is currently available; all configured keys are unavailable or in cooldown.")

        last_error: Optional[BaseException] = None
        attempted = 0
        for index in candidates:
            attempted += 1
            logger.info("Trying API %d...", index + 1)
            try:
                response = self._client(index).chat.completions.create(**kwargs)
            except Exception as exc:  # SDK exception types vary by version.
                last_error = exc
                kind = classify_failure(exc)
                status = _safe_status_code(exc)
                # Never log raw provider exception text because SDK errors can
                # contain request/credential details. Classification is logged safely.
                logger.warning(
                    "API %d failed: type=%s status=%s exception=%s",
                    index + 1,
                    kind,
                    status or "unknown",
                    type(exc).__name__,
                )
                if kind == "temporary":
                    self._mark_temporary_failure(index, exc)
                    continue
                if kind == "auth":
                    self._mark_auth_failure(index)
                    continue
                if kind == "permanent":
                    raise AIPermanentRequestError(
                        f"Permanent Cerebras request error on API {index + 1}."
                    ) from exc
                raise AIRequestError(
                    f"Unknown Cerebras error on API {index + 1}; refusing blind rotation."
                ) from exc
            else:
                self._mark_success(index)
                return response

        raise AIRequestError(
            f"All eligible Cerebras APIs failed during this operation after {attempted} attempts.",
            last_error=last_error,
        )

    @property
    def configured_count(self) -> int:
        return len(self._configured)

    def preferred_api_index(self) -> Optional[int]:
        value = self._router_state.get("preferred_api_index")
        return value if isinstance(value, int) else None
