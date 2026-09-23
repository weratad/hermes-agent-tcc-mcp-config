from pathlib import Path
import importlib.util
import json
import sys
import threading
import time


def load():
    root = Path(__file__).resolve().parent.parent
    name = "tcc_layout_artifacts_test"
    spec = importlib.util.spec_from_file_location(name, root / "layout_artifacts.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    mod.is_ai_ask_user_profile = lambda: True
    return mod


def test_normalize_accepts_similar_cards():
    m = load()
    assert m.normalize_layout("similar_cards") == "similar_cards"
    assert m.normalize_layout({"layout": "event_list"}) == "event_list"
    assert m.normalize_layout("nope") is None
    assert m.normalize_layout("similar") == "similar_cards"
    assert m.normalize_layout("list") == "event_list"


def test_store_take_attach():
    m = load()
    m.store_layout("api-sess-1", "similar_cards")
    assert m.take_layout("api-sess-1") == "similar_cards"
    assert m.take_layout("api-sess-1") is None  # consumed
    m.store_layout("k", "prose")
    payload = {"choices": []}
    m.attach_layout_to_payload(payload, "k")
    assert payload["hermes"]["layout"]["mode"] == "prose"
    assert json.loads(json.dumps(payload))["hermes"]["layout"] == {"mode": "prose"}


def test_exact_layout_enum_and_tool_capture():
    m = load()
    assert m.ALLOWED_LAYOUTS == frozenset(
        {
            "similar_cards",
            "event_list",
            "event_detail",
            "venue_cards",
            "artist_cards",
            "compare_zone",
            "compare_value",
            "compare_matrix",
            "compare_store",
            "prose",
            "refuse",
        }
    )
    m.on_post_tool_call(
        tool_name="present_layout",
        args={"layout": "compare_matrix"},
        session_id="tool-session",
    )
    assert m.take_layout("tool-session") == "compare_matrix"


def test_present_layout_handler_accepts_task_id():
    m = load()
    m._session_key_aliases = lambda: []
    msg = m.present_layout(layout="prose", task_id="x")
    assert msg == "Layout presentation mode recorded."
    assert m.take_layout("x") == "prose"


def test_namespaced_layout_tool_capture():
    m = load()
    for tool_name, session_id in (
        ("mcp__x__present_layout", "mcp-session"),
        ("foo.present_layout", "dot-session"),
    ):
        m.on_post_tool_call(
            tool_name=tool_name,
            args={"layout": "event_list"},
            session_id=session_id,
        )
        assert m.take_layout(session_id) == "event_list"


def test_force_retry_runs_for_catalog_turn_despite_stale_layout():
    m = load()
    key = "retry-session"
    m._ensure_armed = lambda: None
    m._session_key_aliases = lambda: [key]
    m.store_layout(key, "prose")
    sys.modules["_tcc_clarify_artifacts_shared"] = {
        "catalog_turns": {key: time.time()},
        "bag": {},
        "lock": threading.Lock(),
    }
    calls = []

    class Agent:
        stream_delta_callback = object()

    result = {"messages": [{"role": "assistant", "content": "catalog"}]}
    returned = m.maybe_retry_layout(
        Agent(),
        result,
        lambda **kwargs: calls.append(kwargs),
    )

    assert returned is result
    assert len(calls) == 1
    assert calls[0]["user_message"] == m.RETRY_USER_MESSAGE
    assert calls[0]["conversation_history"] is result["messages"]
    assert calls[0]["stream_callback"] is None


def test_force_retry_runs_when_clarify_without_layout():
    m = load()
    key = "clarify-session"
    m._ensure_armed = lambda: None
    m._session_key_aliases = lambda: [key]
    m._turn_state.layout_called = False
    sys.modules["_tcc_clarify_artifacts_shared"] = {
        "catalog_turns": {},
        "bag": {
            key: {
                "clarify": {"question": "next?", "choices": ["A", "B"]},
                "expires": time.time() + 60,
            }
        },
        "lock": threading.Lock(),
    }
    calls = []

    class Agent:
        stream_delta_callback = object()

    returned = m.maybe_retry_layout(
        Agent(),
        {"messages": []},
        lambda **kwargs: calls.append(kwargs),
    )

    assert returned == {"messages": []}
    assert len(calls) == 1
    assert "present_layout" in calls[0]["user_message"]


def test_retry_suppressed_when_layout_called_this_turn():
    m = load()
    key = "called-session"
    m._ensure_armed = lambda: None
    m._session_key_aliases = lambda: [key]
    m._turn_state.layout_called = True
    sys.modules["_tcc_clarify_artifacts_shared"] = {
        "catalog_turns": {key: time.time()},
        "bag": {},
        "lock": threading.Lock(),
    }
    calls = []

    returned = m.maybe_retry_layout(
        object(),
        {"messages": []},
        lambda **kwargs: calls.append(kwargs),
    )

    assert returned == {"messages": []}
    assert calls == []


def test_present_layout_rejects_prose_when_catalog_events():
    m = load()
    key = "prose-reject"
    m._session_key_aliases = lambda: [key]
    sys.modules["_tcc_catalog_artifacts_shared"] = {
        "bag": {key: {"events": [{"title": "A", "url": "/concert/1"}], "expires": time.time() + 60}},
        "lock": threading.Lock(),
    }
    msg = m.present_layout(layout="prose", session_id=key)
    assert "event_list" in msg
    assert m.peek_stored_layout(key) is None
    assert m.take_layout(key) is None


def test_force_retry_when_prose_with_catalog_events():
    m = load()
    key = "prose-retry"
    m._ensure_armed = lambda: None
    m._session_key_aliases = lambda: [key]
    m._turn_state.layout_called = True
    m.store_layout(key, "prose")
    m._mark_layout_called(key)
    sys.modules["_tcc_catalog_artifacts_shared"] = {
        "bag": {key: {"events": [{"title": "A"}], "expires": time.time() + 60}},
        "lock": threading.Lock(),
    }
    sys.modules["_tcc_clarify_artifacts_shared"] = {
        "catalog_turns": {key: time.time()},
        "bag": {},
        "lock": threading.Lock(),
    }
    calls = []

    class Agent:
        stream_delta_callback = object()

    returned = m.maybe_retry_layout(
        Agent(),
        {"messages": [{"role": "assistant", "content": "list"}]},
        lambda **kwargs: calls.append(kwargs),
    )

    assert returned == {"messages": [{"role": "assistant", "content": "list"}]}
    assert len(calls) == 1
    assert calls[0]["user_message"] == m.RETRY_PROSE_WITH_CATALOG_MESSAGE
    assert m.peek_stored_layout(key) is None


def test_price_compare_reply_uses_get_event_tiers_and_compare_value():
    m = load()
    key = "price-compare"
    m._ensure_armed = lambda: None
    m._session_key_aliases = lambda: [key]
    m.store_layout(key, "event_detail")
    m._mark_layout_called(key)
    sys.modules["_tcc_catalog_artifacts_shared"] = {
        "bag": {
            key: {
                "events": [
                    {
                        "title": "Sakon Festival",
                        "venue": "สกลนคร",
                        "layout": "poster",
                        "ticket_tiers": [
                            {"zone": "Early Bird", "price_min": 888},
                            {"zone": "VIP", "price_min": 10000},
                        ],
                    }
                ],
                "expires": time.time() + 60,
            }
        },
        "last_events": {},
        "lock": threading.Lock(),
    }

    result = m.maybe_retry_layout(
        object(),
        {
            "final_response": "งานนี้มีบัตรหลายราคาให้เลือกครับ",
            "messages": [
                {"role": "user", "content": "เทียบราคาบัตร Sakon Festival"},
                {"role": "assistant", "content": "งานนี้มีบัตรหลายราคาให้เลือกครับ"},
            ],
        },
        lambda **kwargs: None,
    )

    assert "⚖️" in result["final_response"]
    assert result["final_response"].count("🎫") == 2
    assert result["messages"][-1]["content"] == result["final_response"]
    assert m.peek_stored_layout(key) == "compare_value"


def test_price_compare_reply_uses_find_events_card_with_most_tiers():
    m = load()
    key = "price-compare-card"
    m._ensure_armed = lambda: None
    m._session_key_aliases = lambda: [key]
    m.store_layout(key, "event_list")
    m._mark_layout_called(key)
    sys.modules["_tcc_catalog_artifacts_shared"] = {
        "bag": {
            key: {
                "events": [
                    {
                        "title": "Other Festival",
                        "layout": "poster",
                        "ticket_tiers": [
                            {"zone": "GA", "price_min": 500},
                            {"zone": "VIP", "price_min": 1500},
                        ],
                    },
                    {
                        "title": "Sakon Festival",
                        "venue": "สกลนคร",
                        "layout": "card",
                        "ticket_tiers": [
                            {"zone": "Early Bird", "price_min": 888},
                            {"zone": "Regular", "price_min": 1800},
                            {"zone": "VIP", "price_min": 10000},
                        ],
                    },
                ],
                "expires": time.time() + 60,
            }
        },
        "last_events": {},
        "lock": threading.Lock(),
    }

    result = m.maybe_retry_layout(
        object(),
        {
            "final_response": "Sakon Festival มีบัตรหลายราคาให้เลือกครับ",
            "messages": [
                {"role": "user", "content": "เทียบราคาบัตร Sakon Festival"},
                {"role": "assistant", "content": "Sakon Festival มีบัตรหลายราคาให้เลือกครับ"},
            ],
        },
        lambda **kwargs: None,
    )

    assert "⚖️" in result["final_response"]
    assert result["final_response"].count("🎫") == 3
    assert "Sakon Festival" in result["final_response"]
    assert m.peek_stored_layout(key) == "compare_value"


if __name__ == "__main__":
    test_normalize_accepts_similar_cards()
    test_store_take_attach()
    test_exact_layout_enum_and_tool_capture()
    test_present_layout_handler_accepts_task_id()
    test_namespaced_layout_tool_capture()
    test_force_retry_runs_for_catalog_turn_despite_stale_layout()
    test_force_retry_runs_when_clarify_without_layout()
    test_retry_suppressed_when_layout_called_this_turn()
    test_present_layout_rejects_prose_when_catalog_events()
    test_force_retry_when_prose_with_catalog_events()
    test_price_compare_reply_uses_get_event_tiers_and_compare_value()
    test_price_compare_reply_uses_find_events_card_with_most_tiers()
    print("tcc-layout-artifacts ok")
