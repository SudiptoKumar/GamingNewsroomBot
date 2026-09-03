import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ai_router import (
    AIRouter,
    AIPermanentRequestError,
    AIRequestError,
)


class FakeResponse:
    def __init__(self, payload="ok"):
        self.payload = payload


class FakeCompletions:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        action = self.behavior.pop(0) if self.behavior else "success"
        if isinstance(action, BaseException):
            raise action
        return FakeResponse(action)


class FakeClient:
    def __init__(self, behavior):
        self.completions = FakeCompletions(behavior)
        self.chat = type("Chat", (), {"completions": self.completions})()


class Clock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def exc(status=None, message="provider failure"):
    error = RuntimeError(message)
    if status is not None:
        error.status_code = status
    return error


def make_router(env, state=None, behaviors=None, clock=None, save_log=None):
    state = {} if state is None else state
    clock = clock or Clock()
    behaviors = behaviors or {}
    clients = {}
    # Keep test mapping simple and robust for arbitrary secret-like values.
    key_to_slot = {}
    for slot in range(1, 11):
        value = env.get(f"CEREBRAS_API_KEY_{slot}")
        if value:
            key_to_slot[value] = slot
    if env.get("CEREBRAS_API_KEY") and not env.get("CEREBRAS_API_KEY_1"):
        key_to_slot[env["CEREBRAS_API_KEY"]] = 1

    def factory(key):
        slot = key_to_slot[key]
        client = FakeClient(list(behaviors.get(slot, ["success"])))
        clients[slot] = client
        return client

    def save(value):
        if save_log is not None:
            save_log.append(value["ai_router"]["preferred_api_index"])

    router = AIRouter(
        model="test-model",
        state=state,
        save_state=save,
        env=env,
        max_keys=10,
        client_factory=factory,
        time_fn=clock,
        sleep_fn=lambda _: None,
    )
    # API 1 -> key-1, API 2 -> key-2, etc.
    return router, clients


def test_api1_success_remains_preferred():
    state = {}
    router, clients = make_router({"CEREBRAS_API_KEY_1": "key-1", "CEREBRAS_API_KEY_2": "key-2"}, state)
    assert router.create(messages=[]).payload == "success"
    assert router.preferred_api_index() == 0
    assert clients[1].completions.calls == 1


def test_api1_429_fails_over_to_api2_and_persists_preference():
    state = {}
    router, clients = make_router(
        {"CEREBRAS_API_KEY_1": "key-1", "CEREBRAS_API_KEY_2": "key-2"},
        state,
        behaviors={1: [exc(429)], 2: ["ok-from-2"]},
    )
    assert router.create(messages=[]).payload == "ok-from-2"
    assert router.preferred_api_index() == 1
    assert state["ai_router"]["api_status"]["0"]["status"] == "cooldown"
    assert clients[1].completions.calls == 1
    assert clients[2].completions.calls == 1


def test_preferred_api_persists_across_router_instances():
    state = {}
    env = {"CEREBRAS_API_KEY_1": "key-1", "CEREBRAS_API_KEY_2": "key-2"}
    router1, _ = make_router(env, state, behaviors={1: [exc(429)], 2: ["ok"]})
    router1.create(messages=[])
    router2, clients2 = make_router(env, state, behaviors={2: ["preferred"]})
    assert router2.create(messages=[]).payload == "preferred"
    assert clients2[2].completions.calls == 1
    assert 1 not in clients2


def test_api2_failure_api3_success_makes_api3_preferred():
    env = {"CEREBRAS_API_KEY_1": "key-1", "CEREBRAS_API_KEY_2": "key-2", "CEREBRAS_API_KEY_3": "key-3"}
    state = {"ai_router": {"preferred_api_index": 1, "api_status": {}}}
    router, clients = make_router(env, state, behaviors={2: [exc(503)], 3: ["ok-3"]})
    assert router.create(messages=[]).payload == "ok-3"
    assert router.preferred_api_index() == 2
    assert clients[2].completions.calls == 1
    assert clients[3].completions.calls == 1


def test_unconfigured_slots_are_skipped():
    env = {"CEREBRAS_API_KEY_1": "key-1", "CEREBRAS_API_KEY_3": "key-3", "CEREBRAS_API_KEY_5": "key-5"}
    state = {"ai_router": {"preferred_api_index": 0, "api_status": {}}}
    router, clients = make_router(env, state, behaviors={1: [exc(429)], 2: ["should-not-be-used"], 3: [exc(429)], 5: ["ok-5"]})
    assert router.create(messages=[]).payload == "ok-5"
    assert 1 in clients and 3 in clients
    assert 2 not in clients


def test_only_api1_works_normally():
    router, clients = make_router({"CEREBRAS_API_KEY_1": "key-1"})
    assert router.create(messages=[]).payload == "success"
    assert clients[1].completions.calls == 1


@pytest.mark.parametrize("count", [2, 3])
def test_contiguous_configurations(count):
    env = {f"CEREBRAS_API_KEY_{i}": f"key-{i}" for i in range(1, count + 1)}
    router, clients = make_router(env, behaviors={1: ["success"]})
    router.create(messages=[])
    assert clients[1].completions.calls == 1


def test_all_ten_failover_and_success():
    env = {f"CEREBRAS_API_KEY_{i}": f"key-{i}" for i in range(1, 11)}
    behaviors = {i: [exc(503)] for i in range(1, 10)}
    behaviors[10] = ["ok-10"]
    router, clients = make_router(env, behaviors=behaviors)
    assert router.create(messages=[]).payload == "ok-10"
    assert router.preferred_api_index() == 9
    assert all(clients[i].completions.calls == 1 for i in range(1, 11))


def test_all_configured_temporarily_fail_gracefully():
    env = {f"CEREBRAS_API_KEY_{i}": f"key-{i}" for i in range(1, 4)}
    behaviors = {i: [exc(503)] for i in range(1, 4)}
    router, _ = make_router(env, behaviors=behaviors)
    with pytest.raises(AIRequestError):
        router.create(messages=[])


def test_expired_cooldown_becomes_eligible_again():
    clock = Clock()
    env = {"CEREBRAS_API_KEY_1": "key-1", "CEREBRAS_API_KEY_2": "key-2"}
    state = {"ai_router": {"preferred_api_index": 0, "api_status": {"0": {"status": "cooldown", "retry_after": 900}}}}
    router, clients = make_router(env, state, behaviors={1: ["recovered"]}, clock=clock)
    assert router.create(messages=[]).payload == "recovered"
    assert clients[1].completions.calls == 1


def test_healthy_preferred_api_does_not_probe_other_keys():
    env = {"CEREBRAS_API_KEY_1": "key-1", "CEREBRAS_API_KEY_2": "key-2"}
    state = {"ai_router": {"preferred_api_index": 1, "api_status": {}}}
    router, clients = make_router(env, state, behaviors={2: ["preferred"]})
    router.create(messages=[])
    assert clients[2].completions.calls == 1
    assert 1 not in clients


def test_permanent_request_error_does_not_rotate():
    env = {"CEREBRAS_API_KEY_1": "key-1", "CEREBRAS_API_KEY_2": "key-2"}
    router, clients = make_router(env, behaviors={1: [exc(400)]})
    with pytest.raises(AIPermanentRequestError):
        router.create(messages=[])
    assert clients[1].completions.calls == 1
    assert 2 not in clients


def test_missing_state_initializes_first_configured_as_preferred():
    env = {"CEREBRAS_API_KEY_2": "key-2", "CEREBRAS_API_KEY_4": "key-4"}
    router, _ = make_router(env)
    assert router.preferred_api_index() == 1


def test_corrupted_router_state_recovers():
    env = {"CEREBRAS_API_KEY_1": "key-1", "CEREBRAS_API_KEY_3": "key-3"}
    state = {"ai_router": "corrupted"}
    router, _ = make_router(env, state)
    assert router.preferred_api_index() == 0
    assert isinstance(state["ai_router"]["api_status"], dict)


def test_successful_request_calls_only_one_api():
    env = {"CEREBRAS_API_KEY_1": "key-1", "CEREBRAS_API_KEY_2": "key-2"}
    router, clients = make_router(env, behaviors={1: ["ok"]})
    router.create(messages=[])
    assert clients[1].completions.calls == 1
    assert 2 not in clients


def test_auth_failure_skips_invalid_slot_and_does_not_log_secret(caplog):
    secret = "VERY_SECRET_KEY_VALUE"
    env = {"CEREBRAS_API_KEY_1": secret, "CEREBRAS_API_KEY_2": "key-2"}
    router, _ = make_router(env, behaviors={1: [exc(401, f"unauthorized {secret}")], 2: ["ok"]})
    with caplog.at_level(logging.INFO):
        assert router.create(messages=[]).payload == "ok"
    assert secret not in "\n".join(record.getMessage() for record in caplog.records)


def test_preferred_index_state_is_persisted():
    env = {"CEREBRAS_API_KEY_1": "key-1", "CEREBRAS_API_KEY_2": "key-2"}
    state = {}
    saved = []
    router, _ = make_router(env, state, behaviors={1: [exc(429)], 2: ["ok"]}, save_log=saved)
    router.create(messages=[])
    assert saved[-1] == 1


def test_legacy_key_is_slot_one():
    env = {"CEREBRAS_API_KEY": "legacy-key", "CEREBRAS_API_KEY_2": "key-2"}
    router, clients = make_router(env, behaviors={1: ["legacy-ok"]})
    assert router.create(messages=[]).payload == "legacy-ok"
    assert clients[1].completions.calls == 1
    assert router.preferred_api_index() == 0


def test_explicit_slot1_overrides_legacy_key():
    env = {"CEREBRAS_API_KEY": "legacy-key", "CEREBRAS_API_KEY_1": "key-1", "CEREBRAS_API_KEY_2": "key-2"}
    router, clients = make_router(env, behaviors={1: ["explicit-ok"]})
    assert router.create(messages=[]).payload == "explicit-ok"
    assert clients[1].completions.calls == 1


def test_retry_after_header_controls_cooldown():
    class RetryAfterError(RuntimeError):
        status_code = 429
        headers = {"Retry-After": "300"}

    clock = Clock()
    state = {}
    env = {"CEREBRAS_API_KEY_1": "key-1", "CEREBRAS_API_KEY_2": "key-2"}
    router, _ = make_router(env, state, behaviors={1: [RetryAfterError()], 2: ["ok"]}, clock=clock)
    router.create(messages=[])
    assert state["ai_router"]["api_status"]["0"]["retry_after"] == 1300.0


def test_preferred_unconfigured_state_rehomes_to_first_configured():
    env = {"CEREBRAS_API_KEY_3": "key-3", "CEREBRAS_API_KEY_5": "key-5"}
    state = {"ai_router": {"preferred_api_index": 0, "api_status": {}}}
    router, _ = make_router(env, state)
    assert router.preferred_api_index() == 2


def test_failed_preferred_skips_cooldown_on_next_request():
    env = {"CEREBRAS_API_KEY_1": "key-1", "CEREBRAS_API_KEY_2": "key-2"}
    state = {}
    router, clients = make_router(env, state, behaviors={1: [exc(429)], 2: ["first-2"],})
    assert router.create(messages=[]).payload == "first-2"
    # API 2 is now preferred; it should be used directly on the next operation.
    clients[2].completions.behavior.append("second-2")
    assert router.create(messages=[]).payload == "second-2"
    assert clients[2].completions.calls == 2


def test_no_configured_keys_fail_cleanly():
    router, _ = make_router({})
    with pytest.raises(AIRequestError):
        router.create(messages=[])


def test_model_mismatch_is_permanent_and_does_not_rotate():
    env = {"CEREBRAS_API_KEY_1": "key-1", "CEREBRAS_API_KEY_2": "key-2"}
    router, clients = make_router(env)
    with pytest.raises(AIPermanentRequestError):
        router.create(model="other-model", messages=[])
    assert not clients
