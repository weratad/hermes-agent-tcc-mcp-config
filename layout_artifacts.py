"""Attach ``present_layout`` choices to OpenAI-compatible responses."""

from __future__ import annotations

import logging
import threading
import time
from functools import wraps
from typing import Any, Dict, List, Optional


from pathlib import Path as _Path
import importlib.util as _ilu

def _load_ai_ask_profile():
    try:
        from . import ai_ask_profile as mod  # type: ignore
        return mod
    except ImportError:
        pass
    path = _Path(__file__).resolve().parent / "ai_ask_profile.py"
    spec = _ilu.spec_from_file_location("_tcc_ai_ask_profile", path)
    mod = _ilu.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod

_ai_ask_profile = _load_ai_ask_profile()
is_ai_ask_user_profile = _ai_ask_profile.is_ai_ask_user_profile


def _load_price_compare_format():
    try:
        from . import price_compare_format as mod  # type: ignore
        return mod
    except ImportError:
        pass
    path = _Path(__file__).resolve().parent / "price_compare_format.py"
    spec = _ilu.spec_from_file_location("_tcc_price_compare_format", path)
    mod = _ilu.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


_price_compare_format = _load_price_compare_format()




_log = logging.getLogger("hermes.plugin.tcc-mcp-config.layout-artifacts")
_TTL_SEC = 300
_PATCHED_ATTR = "_tcc_layout_artifacts_patched"
_AGENT_PATCHED_ATTR = "_tcc_layout_agent_patched"
_RETRY_WRAPPED_ATTR = "_tcc_layout_retry_wrapped"
_retry_guard = threading.local()
_turn_state = threading.local()

ALLOWED_LAYOUTS = frozenset(
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

# Common model slips → canonical enum (keep attach reliable).
LAYOUT_ALIASES = {
    "similar": "similar_cards",
    "similar_card": "similar_cards",
    "cards": "similar_cards",
    "card_grid": "similar_cards",
    "list": "event_list",
    "events": "event_list",
    "event": "event_list",
    "detail": "event_detail",
    "venue": "venue_cards",
    "venues": "venue_cards",
    "artist": "artist_cards",
    "artists": "artist_cards",
    "compare": "compare_matrix",
    "zone": "compare_zone",
    "price": "compare_value",
    "value": "compare_value",
    "matrix": "compare_matrix",
    "store": "compare_store",
    "text": "prose",
    "off_topic": "refuse",
    "off-topic": "refuse",
}

RETRY_USER_MESSAGE = """You did not call present_layout for this turn.
Call present_layout now with exactly one layout from:
similar_cards, event_list, event_detail, venue_cards, artist_cards,
compare_zone, compare_value, compare_matrix, compare_store, prose, refuse.
Off-topic refuse → refuse. Catalog list/recommend → event_list (not prose).
Single-event detail → event_detail. Similar → similar_cards.
Any comparison → compare_zone / compare_value / compare_matrix / compare_store (table), never prose.
Do not rewrite the reply or event list."""

RETRY_PROSE_WITH_CATALOG_MESSAGE = """You called present_layout with prose but this turn has catalog events.
Call present_layout now with event_list (list/recommend) or event_detail (one show) — never prose when catalog events are shown.
Do not rewrite the reply or event list."""

PRESENT_LAYOUT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "present_layout",
        "description": (
            "Choose the UI layout for the current response. "
            "When catalog events were returned, use event_list or event_detail — never prose. "
            "Comparisons must use compare_zone, compare_value, compare_matrix, or compare_store (table UI)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layout": {
                    "type": "string",
                    "enum": sorted(ALLOWED_LAYOUTS),
                }
            },
            "required": ["layout"],
            "additionalProperties": False,
        },
    },
}


def normalize_layout(raw: Any) -> Optional[str]:
    if isinstance(raw, dict):
        raw = raw.get("layout") or raw.get("mode") or raw.get("name")
    mode = str(raw or "").strip()
    if not mode:
        return None
    if mode in ALLOWED_LAYOUTS:
        return mode
    collapsed = mode.lower().replace(" ", "_").replace("-", "_")
    aliased = LAYOUT_ALIASES.get(collapsed)
    if aliased in ALLOWED_LAYOUTS:
        return aliased
    # Last-resort substring match for common slips like "layout=similar_cards".
    for allowed in ALLOWED_LAYOUTS:
        if allowed in collapsed:
            return allowed
    return None


def _shared_state() -> Dict[str, Any]:
    import sys

    state = sys.modules.setdefault(
        "_tcc_layout_artifacts_shared",
        {"bag": {}, "lock": threading.Lock(), "armed": False, "layout_called": {}},
    )
    state.setdefault("armed", False)
    state.setdefault("layout_called", {})
    return state  # type: ignore[return-value]


def _mark_layout_called(*keys: str) -> None:
    """Process-wide flag — tool handlers may run on another thread than retry."""
    now = time.time()
    called = _shared_state()["layout_called"]
    with _lock():
        for raw in keys:
            key = str(raw or "").strip()
            if key:
                called[key] = now
        for key in [k for k, ts in called.items() if now - float(ts) > _TTL_SEC]:
            called.pop(key, None)


def _layout_called_this_turn(*keys: str) -> bool:
    now = time.time()
    called = _shared_state()["layout_called"]
    with _lock():
        for raw in keys:
            key = str(raw or "").strip()
            if not key:
                continue
            ts = called.get(key)
            if ts is not None and now - float(ts) <= _TTL_SEC:
                return True
        return bool(getattr(_turn_state, "layout_called", False))


def _bag() -> Dict[str, Dict[str, Any]]:
    return _shared_state()["bag"]


def _lock() -> threading.Lock:
    return _shared_state()["lock"]


def _purge_expired(now: Optional[float] = None) -> None:
    ts = time.time() if now is None else now
    bag = _bag()
    for key in [key for key, row in bag.items() if row["expires"] < ts]:
        bag.pop(key, None)


def store_layout(key: str, layout: Any) -> None:
    session_key = str(key or "").strip()
    mode = normalize_layout(layout)
    if not session_key or not mode:
        return
    with _lock():
        _purge_expired()
        _bag()[session_key] = {
            "layout": mode,
            "expires": time.time() + _TTL_SEC,
        }


def peek_layout(*keys: str) -> bool:
    with _lock():
        _purge_expired()
        return any(
            str(raw or "").strip() in _bag()
            for raw in keys
            if str(raw or "").strip()
        )


def peek_stored_layout(*keys: str) -> Optional[str]:
    with _lock():
        _purge_expired()
        for raw in keys:
            key = str(raw or "").strip()
            row = _bag().get(key)
            if not key or not row:
                continue
            mode = normalize_layout(row.get("layout"))
            if mode:
                return mode
        return None


def _clear_layout_markers(*keys: str) -> None:
    called = _shared_state()["layout_called"]
    with _lock():
        for raw in keys:
            key = str(raw or "").strip()
            if key:
                called.pop(key, None)
                _bag().pop(key, None)
    _turn_state.layout_called = False


def take_layout(*keys: str) -> Optional[str]:
    with _lock():
        _purge_expired()
        found = None
        for raw in keys:
            key = str(raw or "").strip()
            row = _bag().get(key)
            if key and row:
                found = normalize_layout(row.get("layout"))
                if found:
                    break
        if found:
            for raw in keys:
                key = str(raw or "").strip()
                if key:
                    _bag().pop(key, None)
        return found


def attach_layout_to_payload(
    payload: Dict[str, Any], *keys: str
) -> Dict[str, Any]:
    if not is_ai_ask_user_profile():
        return payload if isinstance(payload, dict) else payload
    if not isinstance(payload, dict):
        return payload
    layout = take_layout(*keys)
    if not layout:
        return payload
    hermes = payload.get("hermes")
    if not isinstance(hermes, dict):
        hermes = {}
        payload["hermes"] = hermes
    hermes["layout"] = {"mode": layout}
    return payload


def _session_key_aliases() -> List[str]:
    aliases: List[str] = []
    try:
        from tools.approval import get_current_session_key

        key = str(get_current_session_key(default="") or "").strip()
        if key:
            aliases.append(key)
    except Exception:
        pass
    try:
        from gateway.session_context import get_session_env

        for name in ("HERMES_SESSION_ID", "HERMES_SESSION_KEY"):
            key = str(get_session_env(name, "") or "").strip()
            if key and key not in aliases:
                aliases.append(key)
    except Exception:
        pass
    return aliases


def _layout_storage_keys(
    session_id: str = "",
    api_request_id: str = "",
    task_id: str = "",
) -> List[str]:
    keys: List[str] = []
    for raw in (session_id, api_request_id, task_id, *_session_key_aliases()):
        key = str(raw or "").strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def present_layout(layout: Any = None, **kwargs: Any) -> str:
    """Record the layout chosen by the model.

    Accept any gateway kwargs (session_id / api_request_id / task_id / …).
    """
    if not is_ai_ask_user_profile():
        return "Layout tools are only available on user-* AI Ask profiles."
    mode = normalize_layout(layout if layout is not None else kwargs.get("layout"))
    if not mode:
        _log.warning(
            "layout artifacts: invalid layout raw=%r kwargs_keys=%s",
            layout if layout is not None else kwargs.get("layout"),
            sorted(str(k) for k in kwargs.keys())[:12],
        )
        return "Invalid layout. Use one of: " + ", ".join(sorted(ALLOWED_LAYOUTS))
    keys = _layout_storage_keys(
        str(kwargs.get("session_id") or ""),
        str(kwargs.get("api_request_id") or ""),
        str(kwargs.get("task_id") or ""),
    )
    if mode == "prose" and _peek_catalog_events(*keys):
        return (
            "Invalid layout: catalog events are present this turn. "
            "Call present_layout with event_list or event_detail, not prose."
        )
    for key in keys:
        store_layout(key, mode)
    _turn_state.layout_called = True
    _mark_layout_called(*keys)
    if not keys:
        _log.warning("layout artifacts: present_layout had no session key")
    return "Layout presentation mode recorded."


def on_post_tool_call(
    *,
    tool_name: str = "",
    args: Any = None,
    result: Any = None,
    session_id: str = "",
    task_id: str = "",
    api_request_id: str = "",
    **_: Any,
) -> None:
    if not is_ai_ask_user_profile():
        return
    del result
    _ensure_armed()
    name = str(tool_name or "").strip()
    leaf = name.replace("__", ".").split(".")[-1]
    if not (name.endswith("present_layout") or leaf == "present_layout"):
        return
    mode = normalize_layout(args)
    if not mode:
        return
    keys = _layout_storage_keys(session_id, api_request_id, task_id)
    if mode == "prose" and _peek_catalog_events(*keys):
        return
    for key in keys:
        store_layout(key, mode)
    _turn_state.layout_called = True
    _mark_layout_called(session_id, api_request_id, task_id, *_session_key_aliases())


def _peek_catalog_turn(*keys: str) -> bool:
    import sys

    state = sys.modules.get("_tcc_clarify_artifacts_shared")
    if not isinstance(state, dict):
        return False
    turns = state.get("catalog_turns")
    if not isinstance(turns, dict):
        return False
    lock = state.get("lock")

    def _has() -> bool:
        now = time.time()
        return any(
            key in turns and now - float(turns[key]) <= _TTL_SEC
            for key in (str(raw or "").strip() for raw in keys)
            if key
        )

    if lock is not None:
        with lock:
            return _has()
    return _has()


def _peek_catalog_events(*keys: str) -> bool:
    import sys

    state = sys.modules.get("_tcc_catalog_artifacts_shared")
    if not isinstance(state, dict):
        return False
    bag = state.get("bag")
    if not isinstance(bag, dict):
        return False
    lock = state.get("lock")

    def _has() -> bool:
        return any(
            isinstance(bag.get(key), dict)
            and bool(bag[key].get("events"))
            for key in (str(raw or "").strip() for raw in keys)
            if key
        )

    if lock is not None:
        with lock:
            return _has()
    return _has()


def _peek_clarify(*keys: str) -> bool:
    """True when clarify chips were stored this turn (covers refuse + chips)."""
    import sys

    state = sys.modules.get("_tcc_clarify_artifacts_shared")
    if not isinstance(state, dict):
        return False
    bag = state.get("bag")
    if not isinstance(bag, dict):
        return False
    lock = state.get("lock")

    def _has() -> bool:
        for raw in keys:
            key = str(raw or "").strip()
            if not key:
                continue
            row = bag.get(key)
            if not isinstance(row, dict):
                continue
            clarify = row.get("clarify")
            if isinstance(clarify, dict) and clarify.get("choices"):
                return True
        return False

    if lock is not None:
        with lock:
            return _has()
    return _has()


def maybe_retry_layout(agent: Any, result: Any, run_conversation) -> Any:
    _ensure_armed()
    if not is_ai_ask_user_profile():
        return result
    result = _ensure_price_compare_artifacts(result)
    if getattr(_retry_guard, "active", False):
        result = _ensure_usecase_artifacts(result)
        return result
    keys = _session_key_aliases()
    has_events = _peek_catalog_events(*keys)
    has_catalog = (
        _peek_catalog_turn(*keys)
        or has_events
        or _peek_clarify(*keys)
    )
    if has_catalog:
        stored = peek_stored_layout(*keys)
        prose_with_events = stored == "prose" and has_events
        missing = not _layout_called_this_turn(*keys)
        if missing or prose_with_events:
            retry_message = (
                RETRY_PROSE_WITH_CATALOG_MESSAGE
                if prose_with_events
                else RETRY_USER_MESSAGE
            )
            if prose_with_events:
                _clear_layout_markers(*keys)

            history = result.get("messages") if isinstance(result, dict) else None
            saved_delta = getattr(agent, "stream_delta_callback", None)
            _retry_guard.active = True
            try:
                if saved_delta is not None:
                    agent.stream_delta_callback = None
                run_conversation(
                    user_message=retry_message,
                    conversation_history=history,
                    stream_callback=None,
                )
            except Exception:
                _log.exception("layout artifacts: retry failed")
            finally:
                if saved_delta is not None:
                    agent.stream_delta_callback = saved_delta
                _retry_guard.active = False
    return _ensure_usecase_artifacts(result)


def _load_usecase_packs():
    try:
        from . import usecase_packs as mod  # type: ignore

        return mod
    except ImportError:
        pass
    path = _Path(__file__).resolve().parent / "usecase_packs.py"
    spec = _ilu.spec_from_file_location("_tcc_usecase_packs", path)
    mod = _ilu.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _peek_catalog_event_rows(*keys: str) -> list:
    import sys

    state = sys.modules.get("_tcc_catalog_artifacts_shared")
    if not isinstance(state, dict):
        return []
    # Prefer catalog.peek_events (includes last_events after take).
    peek = state.get("peek_events")
    if callable(peek):
        try:
            rows = peek(*keys)
            if isinstance(rows, list) and rows:
                return list(rows)
        except Exception:
            pass
    bag = state.get("bag")
    if not isinstance(bag, dict):
        return []
    lock = state.get("lock")
    last = state.get("last_events")
    if not isinstance(last, dict):
        last = {}

    def _read() -> list:
        for raw in keys:
            key = str(raw or "").strip()
            if not key:
                continue
            row = bag.get(key)
            if isinstance(row, dict):
                events = row.get("events")
                if isinstance(events, list) and events:
                    return list(events)
        now = time.time()
        for raw in keys:
            key = str(raw or "").strip()
            if not key:
                continue
            row = last.get(key)
            if not isinstance(row, dict):
                continue
            if float(row.get("expires") or 0) < now:
                continue
            events = row.get("events")
            if isinstance(events, list) and events:
                return list(events)
        return []

    if lock is not None:
        with lock:
            return _read()
    return _read()


def ensure_price_compare_reply(session_key: str, user_text: str, reply: str) -> str:
    """Force deterministic compare markers when get_event returned usable tiers."""
    if not _price_compare_format.is_price_compare_ask(user_text):
        return reply

    keys = _layout_storage_keys(str(session_key or ""))
    events = _peek_catalog_event_rows(*keys)
    candidates = [
        (
            len(_price_compare_format.usable_tiers(row.get("ticket_tiers") or [])),
            row.get("layout") == "poster",
            row,
        )
        for row in events
        if isinstance(row, dict)
    ]
    event = max(
        (candidate for candidate in candidates if candidate[0] >= 2),
        key=lambda candidate: (candidate[0], candidate[1]),
        default=(0, False, None),
    )[2]
    if not event:
        return reply

    for key in keys:
        store_layout(key, "compare_value")
    _turn_state.layout_called = True
    _mark_layout_called(*keys)

    if _price_compare_format.has_price_compare_markers(reply):
        return reply
    return _price_compare_format.format_price_compare_markers(
        event["ticket_tiers"],
        title=str(event.get("title") or ""),
        venue=str(event.get("venue") or ""),
    )


def _ensure_price_compare_artifacts(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    messages = result.get("messages")
    try:
        user_text = _load_usecase_packs().last_user_text(messages)
    except Exception:
        return result
    keys = _session_key_aliases()
    if not keys or not user_text:
        return result

    reply = result.get("final_response")
    if not isinstance(reply, str):
        reply = ""
        if isinstance(messages, list):
            for row in reversed(messages):
                if isinstance(row, dict) and row.get("role") == "assistant":
                    content = row.get("content")
                    if isinstance(content, str):
                        reply = content
                        break
    ensured = ensure_price_compare_reply(keys[0], user_text, reply)
    if ensured == reply:
        return result

    result["final_response"] = ensured
    if isinstance(messages, list):
        for row in reversed(messages):
            if isinstance(row, dict) and row.get("role") == "assistant":
                row["content"] = ensured
                break
    return result


def _ensure_usecase_artifacts(result: Any) -> Any:
    """Coerce layout + inject hardcoded clarify packs for known use-case steps."""
    if not is_ai_ask_user_profile():
        return result
    try:
        packs = _load_usecase_packs()
    except Exception:
        _log.debug("layout artifacts: usecase_packs unavailable", exc_info=True)
        return result

    keys = _session_key_aliases()
    if not keys:
        return result
    messages = result.get("messages") if isinstance(result, dict) else None
    user_text = packs.last_user_text(messages)
    events = _peek_catalog_event_rows(*keys)
    layout = peek_stored_layout(*keys)
    suggested = packs.suggested_layout(layout, events, user_text)
    if suggested and suggested != layout:
        for key in keys:
            store_layout(key, suggested)
        layout = suggested
        _turn_state.layout_called = True
        _mark_layout_called(*keys)
        _log.info("layout artifacts: usecase coerce layout=%s", suggested)

    pack = packs.select_pack(layout, events, user_text)
    if not pack:
        return result

    # Store clarify via clarify shared bag when pack does not match.
    import sys

    clarify_state = sys.modules.get("_tcc_clarify_artifacts_shared")
    if not isinstance(clarify_state, dict):
        return result
    bag = clarify_state.get("bag")
    lock = clarify_state.get("lock")
    if not isinstance(bag, dict):
        return result

    def _existing_choices() -> list:
        for raw in keys:
            key = str(raw or "").strip()
            row = bag.get(key) if key else None
            if not isinstance(row, dict):
                continue
            clarify = row.get("clarify")
            if isinstance(clarify, dict) and isinstance(clarify.get("choices"), list):
                return list(clarify["choices"])
        return []

    if lock is not None:
        with lock:
            existing = _existing_choices()
    else:
        existing = _existing_choices()

    if packs.pack_matches(existing, pack["choices"]):
        return result

    clarify = {
        "question": str(pack.get("question") or "สนใจต่อยังไงดี?"),
        "choices": list(pack.get("choices") or [])[:4],
    }
    expires = time.time() + _TTL_SEC
    if lock is not None:
        with lock:
            for key in keys:
                if key:
                    bag[key] = {"clarify": clarify, "expires": expires}
    else:
        for key in keys:
            if key:
                bag[key] = {"clarify": clarify, "expires": expires}
    _log.info(
        "layout artifacts: usecase clarify injected choices=%s",
        clarify["choices"][:4],
    )
    return result


def wrap_agent_run_conversation(agent: Any) -> None:
    original = getattr(agent, "run_conversation", None)
    if not callable(original) or getattr(original, _RETRY_WRAPPED_ATTR, False):
        return

    @wraps(original)
    def run_conversation(*args, **kwargs):
        _turn_state.layout_called = False
        result = original(*args, **kwargs)
        return maybe_retry_layout(agent, result, original)

    setattr(run_conversation, _RETRY_WRAPPED_ATTR, True)
    agent.run_conversation = run_conversation


def _header_get(headers: Any, name: str) -> str:
    if headers is None:
        return ""
    try:
        get = getattr(headers, "get", None)
        if callable(get):
            return str(get(name) or get(name.lower()) or "")
    except Exception:
        pass
    if isinstance(headers, dict):
        for key, value in headers.items():
            if str(key).lower() == name.lower():
                return str(value or "")
    return ""


def _is_our_patch(value: Any) -> bool:
    return bool(value is not None and getattr(value, _PATCHED_ATTR, False))


def _install_agent_patch(api_mod: Any) -> bool:
    armed = False
    for candidate in vars(api_mod).values():
        if not isinstance(candidate, type):
            continue
        original_create_agent = getattr(candidate, "__dict__", {}).get("_create_agent")
        if not callable(original_create_agent):
            continue
        if getattr(original_create_agent, _AGENT_PATCHED_ATTR, False):
            armed = True
            continue

        def make_create_agent(original):
            @wraps(original)
            def _create_agent(self, *args, **kwargs):
                agent = original(self, *args, **kwargs)
                wrap_agent_run_conversation(agent)
                return agent

            setattr(_create_agent, _AGENT_PATCHED_ATTR, True)
            return _create_agent

        setattr(candidate, "_create_agent", make_create_agent(original_create_agent))
        armed = True
    return armed


def _install_response_patch() -> bool:
    try:
        from aiohttp import web
        from gateway.platforms import api_server as api_mod
    except Exception:
        return False

    armed = _install_agent_patch(api_mod)
    if not _is_our_patch(getattr(web, "json_response", None)):
        original_json_response = web.json_response

        @wraps(original_json_response)
        def json_response(*args, **kwargs):
            body = args[0] if args else kwargs.get("data")
            if isinstance(body, dict) and body.get("object") == "chat.completion":
                headers = kwargs.get("headers")
                attach_layout_to_payload(
                    body,
                    _header_get(headers, "X-Hermes-Session-Id"),
                    _header_get(headers, "X-Hermes-Session-Key"),
                    *_session_key_aliases(),
                )
            return original_json_response(*args, **kwargs)

        setattr(json_response, _PATCHED_ATTR, True)
        web.json_response = json_response
        armed = True

    original_frame = getattr(api_mod, "_sse_frame", None)
    if original_frame is not None and not _is_our_patch(original_frame):

        @wraps(original_frame)
        def _sse_frame(data, event=None):
            if isinstance(data, dict) and data.get("object") == "chat.completion.chunk":
                choices = data.get("choices") or []
                if choices and choices[0].get("finish_reason"):
                    attach_layout_to_payload(data, *_session_key_aliases())
            if event is None:
                return original_frame(data)
            return original_frame(data, event=event)

        setattr(_sse_frame, _PATCHED_ATTR, True)
        api_mod._sse_frame = _sse_frame
        armed = True

    if armed:
        _shared_state()["armed"] = True
    return armed


def _ensure_armed(*_args, **_kwargs) -> None:
    try:
        _install_response_patch()
    except Exception:
        _log.debug("layout artifacts: ensure_armed failed", exc_info=True)


def register(ctx) -> None:
    print("tcc-layout-artifacts: register()", flush=True)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_tool(
        name="present_layout",
        toolset="tcc-layout",
        schema=PRESENT_LAYOUT_SCHEMA,
        handler=present_layout,
        description=PRESENT_LAYOUT_SCHEMA["function"]["description"],
        emoji="🖼️",
    )
    _ensure_armed()
    print("tcc-layout-artifacts: ready", flush=True)

    def _arm_loop() -> None:
        for _ in range(90):
            if _install_response_patch():
                return
            time.sleep(2)
        _log.error("tcc-layout-artifacts failed to arm within timeout")

    threading.Thread(target=_arm_loop, name="tcc-layout-arm", daemon=True).start()
