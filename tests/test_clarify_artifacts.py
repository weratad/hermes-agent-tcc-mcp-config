"""Standalone tests for the tcc-clarify-artifacts Hermes plugin.

Run: python3 tests/test_clarify_artifacts.py

Hermes' upstream callback contract is:
    clarify_callback(question: str, choices, multi_select: bool = False) -> str
The callback receives labels after presentation decoration, including an
optional ``(Recommended)`` suffix, and the clarify tool supports at most four
choices. Gateway implementations register it on ``agent.clarify_callback``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


PLUGIN_DIR = Path(__file__).resolve().parent.parent
TOOL_RESULT = (
    "Choices were shown to the user as UI chips. Do not list numbered options "
    "in your reply. Wait for the user's next message as their selection."
)


def load_plugin():
    module_name = "tcc_clarify_artifacts_test"
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(module_name, PLUGIN_DIR / "clarify_artifacts.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.is_ai_ask_user_profile = lambda: True
    return module


def test_normalize_choices_caps_at_4_and_strips_recommended():
    mod = load_plugin()
    out = mod.normalize_clarify(
        {
            "question": "Q?",
            "choices": ["A (Recommended)", "B", "C", "D", "E"],
        }
    )
    assert out["question"] == "Q?"
    assert out["choices"] == ["A", "B", "C", "D"]


def test_attach_clarify_to_payload():
    mod = load_plugin()
    mod.store_clarify(
        "guest-anon", {"question": "Q?", "choices": ["หนึ่ง", "สอง"]}
    )
    payload = {"object": "chat.completion", "choices": []}
    out = mod.attach_clarify_to_payload(payload, "guest-anon")
    assert out["hermes"]["clarify"]["choices"] == ["หนึ่ง", "สอง"]


def test_callback_returns_immediately_without_waiting():
    mod = load_plugin()
    mod._session_key_aliases = lambda: ["guest-anon"]
    result = mod.api_server_clarify_callback(
        question="Q?",
        choices=["หนึ่ง", "สอง"],
    )
    assert result == TOOL_RESULT
    # Bag must already have choices for attach.
    payload = mod.attach_clarify_to_payload({}, "guest-anon")
    assert payload["hermes"]["clarify"]["choices"] == ["หนึ่ง", "สอง"]


def test_agent_patch_wraps_create_agent_and_sets_callback():
    mod = load_plugin()

    class Agent:
        clarify_callback = None

    class APIServerAdapter:
        def _create_agent(self, marker=None):
            agent = Agent()
            agent.marker = marker
            return agent

    original_create_agent = APIServerAdapter._create_agent
    untouched_global_agent = object()
    api_mod = SimpleNamespace(
        APIServerAdapter=APIServerAdapter,
        AIAgent=untouched_global_agent,
    )

    assert mod._install_agent_patch(api_mod)
    agent = APIServerAdapter()._create_agent(marker="kept")

    assert agent.marker == "kept"
    assert agent.clarify_callback is mod.api_server_clarify_callback
    assert APIServerAdapter._create_agent is not original_create_agent
    assert api_mod.AIAgent is untouched_global_agent


def test_agent_patch_wraps_all_matching_classes_only():
    mod = load_plugin()

    class FirstAdapter:
        def _create_agent(self):
            return SimpleNamespace(source="first")

    class SecondAdapter:
        def _create_agent(self):
            return SimpleNamespace(source="second")

    decoy = SimpleNamespace(_create_agent=lambda: SimpleNamespace(source="decoy"))
    api_mod = SimpleNamespace(
        FirstAdapter=FirstAdapter,
        SecondAdapter=SecondAdapter,
        decoy=decoy,
    )

    assert mod._install_agent_patch(api_mod)
    for adapter, source in ((FirstAdapter, "first"), (SecondAdapter, "second")):
        agent = adapter()._create_agent()
        assert agent.source == source
        assert agent.clarify_callback is mod.api_server_clarify_callback
    assert not getattr(decoy._create_agent, mod._AGENT_PATCHED_ATTR, False)


def test_response_wrappers_preserve_markers_and_accept_keyword_data():
    mod = load_plugin()
    saved = {
        name: sys.modules.get(name)
        for name in ("aiohttp", "aiohttp.web", "gateway", "gateway.platforms")
    }
    try:
        def catalog_json_response(*args, **kwargs):
            return args, kwargs

        catalog_json_response._tcc_catalog_artifacts_patched = True

        def catalog_sse_frame(data, event=None):
            return data, event

        catalog_sse_frame._tcc_catalog_artifacts_patched = True

        web = ModuleType("aiohttp.web")
        web.json_response = catalog_json_response
        aiohttp = ModuleType("aiohttp")
        aiohttp.web = web
        api_mod = ModuleType("gateway.platforms.api_server")
        api_mod._sse_frame = catalog_sse_frame
        platforms = ModuleType("gateway.platforms")
        platforms.api_server = api_mod
        gateway = ModuleType("gateway")
        gateway.platforms = platforms
        sys.modules.update(
            {
                "aiohttp": aiohttp,
                "aiohttp.web": web,
                "gateway": gateway,
                "gateway.platforms": platforms,
            }
        )
        mod._session_key_aliases = lambda: ["stream-key"]

        assert mod._install_response_patch()
        assert web.json_response._tcc_catalog_artifacts_patched
        assert getattr(web.json_response, mod._PATCHED_ATTR)
        assert api_mod._sse_frame._tcc_catalog_artifacts_patched
        assert getattr(api_mod._sse_frame, mod._PATCHED_ATTR)

        mod.store_clarify("json-key", {"question": "Q?", "choices": ["A"]})
        body = {"object": "chat.completion", "choices": []}
        web.json_response(data=body, headers={"X-Hermes-Session-Key": "json-key"})
        assert body["hermes"]["clarify"]["choices"] == ["A"]

        mod.store_clarify("stream-key", {"question": "Q?", "choices": ["B"]})
        chunk = {
            "object": "chat.completion.chunk",
            "choices": [{"finish_reason": "stop"}],
        }
        api_mod._sse_frame(chunk)
        assert chunk["hermes"]["clarify"]["choices"] == ["B"]
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def test_attach_does_not_infer_from_prose():
    mod = load_plugin()
    payload = {
        "object": "chat.completion",
        "choices": [
            {
                "message": {
                    "content": (
                        'ถ้าอยาก ผมคัดให้ต่อได้ว่า “คุ้มสุด” หรือ “ศิลปินดังสุด”'
                    )
                }
            }
        ],
    }
    out = mod.attach_clarify_to_payload(payload, "guest-anon")
    assert "hermes" not in out or "clarify" not in (out.get("hermes") or {})


def test_peek_clarify_and_catalog():
    mod = load_plugin()
    import sys

    sys.modules["_tcc_catalog_artifacts_shared"] = {
        "bag": {"guest-anon": {"events": [{"title": "A"}], "expires": 9e12}},
        "lock": __import__("threading").Lock(),
    }
    with mod._lock():
        mod._bag().clear()
    assert mod.peek_catalog_events("guest-anon") is True
    assert mod.peek_catalog_events("missing") is False
    assert mod.peek_clarify("guest-anon") is False
    mod.store_clarify("guest-anon", {"question": "Q?", "choices": ["A", "B"]})
    assert mod.peek_clarify("guest-anon") is True
    with mod._lock():
        mod._bag().clear()


def test_maybe_retry_clarify_runs_once_when_catalog_without_clarify():
    mod = load_plugin()
    with mod._lock():
        mod._bag().clear()
        mod._shared_state()["catalog_turns"].clear()
    mod._session_key_aliases = lambda: ["guest-anon"]
    mod.mark_catalog_turn("guest-anon")
    calls = []

    def fake_run(**kwargs):
        calls.append(kwargs)
        mod.store_clarify(
            "guest-anon", {"question": "คัดต่อ?", "choices": ["คุ้มสุด", "ดังสุด"]}
        )
        return {"final_response": "retry"}

    first = {"final_response": "list of events", "messages": [{"role": "assistant", "content": "list"}]}
    agent = SimpleNamespace(stream_delta_callback=lambda *_: None)
    out = mod.maybe_retry_clarify(agent, first, fake_run)
    assert out is first
    assert len(calls) == 1
    assert calls[0]["user_message"] == mod.RETRY_USER_MESSAGE
    assert mod.peek_clarify("guest-anon") is True


def test_maybe_retry_skipped_when_clarify_already_present():
    mod = load_plugin()
    with mod._lock():
        mod._bag().clear()
        mod._shared_state()["catalog_turns"].clear()
        mod._shared_state()["last_catalog_at"] = None
    mod._session_key_aliases = lambda: ["guest-anon"]
    mod.mark_catalog_turn("guest-anon")
    mod._turn_state.clarify_called = True
    calls = []
    out = mod.maybe_retry_clarify(
        SimpleNamespace(),
        {"final_response": "x"},
        lambda **kwargs: calls.append(kwargs) or {},
    )
    assert out["final_response"] == "x"
    assert calls == []


def test_maybe_retry_skipped_without_catalog():
    mod = load_plugin()
    import sys

    sys.modules["_tcc_catalog_artifacts_shared"] = {
        "bag": {},
        "lock": __import__("threading").Lock(),
    }
    with mod._lock():
        mod._bag().clear()
        state = mod._shared_state()
        state["catalog_turns"].clear()
        state["last_catalog_at"] = None
    mod._session_key_aliases = lambda: ["guest-anon"]
    assert mod.peek_catalog_turn("guest-anon") is False
    assert mod.peek_catalog_events("guest-anon") is False
    calls = []
    mod.maybe_retry_clarify(
        SimpleNamespace(),
        {"final_response": "hello"},
        lambda **kwargs: calls.append(1) or {},
    )
    assert calls == []


def main():
    tests = [
        test_normalize_choices_caps_at_4_and_strips_recommended,
        test_attach_clarify_to_payload,
        test_callback_returns_immediately_without_waiting,
        test_agent_patch_wraps_create_agent_and_sets_callback,
        test_agent_patch_wraps_all_matching_classes_only,
        test_response_wrappers_preserve_markers_and_accept_keyword_data,
        test_attach_does_not_infer_from_prose,
        test_peek_clarify_and_catalog,
        test_maybe_retry_clarify_runs_once_when_catalog_without_clarify,
        test_maybe_retry_skipped_when_clarify_already_present,
        test_maybe_retry_skipped_without_catalog,
    ]
    passed = []
    failed = []
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed.append((test.__name__, exc))
            print(f"  FAIL {test.__name__}: {type(exc).__name__}: {exc}")
        else:
            passed.append(test.__name__)
            print(f"  ok   {test.__name__}")
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
