"""Attach api_server ``clarify`` choices to OpenAI-compatible responses.

Hermes' clarify tool calls ``agent.clarify_callback(question, choices,
multi_select=False)``. Messaging gateways normally block that callback while
waiting for user input. The api_server has no interactive resume channel, so
this plugin stores the prompt for the response and returns immediately.

Chips come only from a real ``clarify`` tool call — this plugin does not
infer choices from reply prose.

When a turn stored catalog events but never called ``clarify``, this plugin
runs one silent follow-up turn that asks the model to call ``clarify`` only,
then keeps the first turn's assistant text for the client.
"""

from __future__ import annotations

import logging
import re
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




_log = logging.getLogger("hermes.plugin.tcc-mcp-config.clarify-artifacts")
_TTL_SEC = 300
_MAX_CHOICES = 4
_RECOMMENDED_RE = re.compile(r"\s*\(Recommended\)\s*$")
_PATCHED_ATTR = "_tcc_clarify_artifacts_patched"
_AGENT_PATCHED_ATTR = "_tcc_clarify_callback_patched"
_RETRY_WRAPPED_ATTR = "_tcc_clarify_retry_wrapped"
_retry_guard = threading.local()
_turn_state = threading.local()
TOOL_RESULT = (
    "Choices were shown to the user as UI chips. Do not list numbered options "
    "in your reply. Wait for the user's next message as their selection."
)
RETRY_USER_MESSAGE = (
    "You returned catalog events but did not call the clarify tool. "
    "Call clarify now with a short question and 2–4 next-step choices "
    "(filters like worth-it / most famous / soonest, or which show to open). "
    "Put every option only in the choices array. "
    "Do not rewrite or re-list the events. Do not write chip/emoji lines."
)


def _shared_state() -> Dict[str, Any]:
    """Share the bag across per-profile plugin module instances."""
    import sys

    state = sys.modules.setdefault(
        "_tcc_clarify_artifacts_shared",
        {
            "bag": {},
            "catalog_turns": {},
            "lock": threading.Lock(),
            "armed": False,
            "last_catalog_at": None,
        },
    )
    state.setdefault("armed", False)
    state.setdefault("catalog_turns", {})
    state.setdefault("last_catalog_at", None)
    return state  # type: ignore[return-value]


def _bag() -> Dict[str, Dict[str, Any]]:
    return _shared_state()["bag"]


def _lock() -> threading.Lock:
    return _shared_state()["lock"]


def _purge_expired(now: Optional[float] = None) -> None:
    ts = time.time() if now is None else now
    bag = _bag()
    for key in [key for key, row in bag.items() if row["expires"] < ts]:
        bag.pop(key, None)


def _clean_choices(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    choices: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        label = _RECOMMENDED_RE.sub("", item).strip()
        if not label or label in choices:
            continue
        choices.append(label)
        if len(choices) == _MAX_CHOICES:
            break
    return choices


def normalize_clarify(args: Any) -> Dict[str, Any]:
    """Normalize one clarify call, using only the first batched question."""
    source = args if isinstance(args, dict) else {}
    questions = source.get("questions")
    if isinstance(questions, list) and questions:
        first = questions[0]
        if isinstance(first, dict):
            source = first
        elif isinstance(first, str):
            source = {"question": first, "choices": source.get("choices")}
    question = str(source.get("question") or "").strip()
    return {"question": question, "choices": _clean_choices(source.get("choices"))}


def store_clarify(session_key: str, payload: Any) -> None:
    key = str(session_key or "").strip()
    clarify = normalize_clarify(payload)
    if not key or not clarify["question"] or not clarify["choices"]:
        return
    with _lock():
        _purge_expired()
        _bag()[key] = {
            "clarify": clarify,
            "expires": time.time() + _TTL_SEC,
        }


def peek_clarify(*keys: str) -> bool:
    return peek_clarify_payload(*keys) is not None


def peek_clarify_payload(*keys: str) -> Optional[Dict[str, Any]]:
    with _lock():
        _purge_expired()
        bag = _bag()
        for raw in keys:
            key = str(raw or "").strip()
            if not key:
                continue
            row = bag.get(key)
            if not row or not isinstance(row.get("clarify"), dict):
                continue
            choices = row["clarify"].get("choices")
            if isinstance(choices, list) and choices:
                return dict(row["clarify"])
        return None


_CATALOG_TOOL_RE = re.compile(
    r"(?:^|__)(find_events|get_event|recommend_events|search_events|"
    r"search_stores|list_stores|get_store)$"
)


def mark_catalog_turn(*keys: str) -> None:
    now = time.time()
    with _lock():
        state = _shared_state()
        state["last_catalog_at"] = now
        turns = state["catalog_turns"]
        for raw in keys:
            key = str(raw or "").strip()
            if key:
                turns[key] = now
        for key in [k for k, ts in turns.items() if now - float(ts) > _TTL_SEC]:
            turns.pop(key, None)


def peek_catalog_turn(*keys: str) -> bool:
    now = time.time()
    with _lock():
        state = _shared_state()
        turns = state["catalog_turns"]
        for raw in keys:
            key = str(raw or "").strip()
            if not key:
                continue
            ts = turns.get(key)
            if ts is not None and now - float(ts) <= _TTL_SEC:
                return True
        last = state.get("last_catalog_at")
        if last is not None and now - float(last) <= 120:
            return True
        return False


def on_post_tool_call(
    *,
    tool_name: str = "",
    result: Any = None,
    session_id: str = "",
    task_id: str = "",
    api_request_id: str = "",
    **_: Any,
) -> None:
    """Re-arm patches and remember catalog tool success for clarify retry."""
    if not is_ai_ask_user_profile():
        return
    del result
    _ensure_armed()
    name = str(tool_name or "")
    if not _CATALOG_TOOL_RE.search(name):
        return
    keys = [
        str(session_id or "").strip(),
        str(api_request_id or "").strip(),
        str(task_id or "").strip(),
        *_session_key_aliases(),
    ]
    keys = [k for k in keys if k] or ["guest-anon"]
    mark_catalog_turn(*keys)
    _log.warning(
        "clarify artifacts: catalog tool %s marked keys=%s",
        name,
        keys[:6],
    )


def peek_catalog_events(*keys: str) -> bool:
    """True when tcc-catalog-artifacts still holds events for this turn."""
    import sys

    state = sys.modules.get("_tcc_catalog_artifacts_shared")
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
            if not row:
                continue
            events = row.get("events")
            if isinstance(events, list) and events:
                return True
        return False

    if lock is not None:
        with lock:
            return _has()
    return _has()


def take_clarify(*keys: str) -> Optional[Dict[str, Any]]:
    with _lock():
        _purge_expired()
        bag = _bag()
        found = None
        for raw in keys:
            key = str(raw or "").strip()
            if not key:
                continue
            row = bag.get(key)
            if row and isinstance(row.get("clarify"), dict):
                found = dict(row["clarify"])
                break
        if found:
            for raw in keys:
                key = str(raw or "").strip()
                if key:
                    bag.pop(key, None)
        return found


def attach_clarify_to_payload(
    payload: Dict[str, Any], *keys: str
) -> Dict[str, Any]:
    if not is_ai_ask_user_profile():
        return payload if isinstance(payload, dict) else payload
    if not isinstance(payload, dict):
        return payload
    key_list = [str(key).strip() for key in keys if str(key or "").strip()]
    clarify = take_clarify(*key_list)
    if not clarify:
        if key_list:
            with _lock():
                bag_keys = list(_bag().keys())
            _log.debug(
                "clarify artifacts: attach miss keys=%s bag=%s object=%s",
                key_list[:6],
                bag_keys[:8],
                payload.get("object"),
            )
        return payload
    hermes = payload.get("hermes")
    if not isinstance(hermes, dict):
        hermes = {}
        payload["hermes"] = hermes
    hermes["clarify"] = clarify
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


def api_server_clarify_callback(
    question: str, choices, multi_select: bool = False
) -> str:
    """Capture choices without blocking the api_server agent worker thread."""
    del multi_select
    if not is_ai_ask_user_profile():
        return "Clarify is only available on user-* AI Ask profiles."
    clarify = normalize_clarify({"question": question, "choices": choices})
    keys = _session_key_aliases()
    for key in keys:
        store_clarify(key, clarify)
    _turn_state.clarify_called = True
    if keys:
        _log.info("clarify artifacts: stored prompt keys=%s", keys[:6])
    else:
        _log.warning("clarify artifacts: callback had no session key")
    return TOOL_RESULT


def maybe_retry_clarify(agent: Any, result: Any, run_conversation) -> Any:
    """One silent follow-up when catalog events exist but clarify was skipped."""
    _ensure_armed()
    if not is_ai_ask_user_profile():
        return result
    if getattr(_retry_guard, "active", False):
        return result
    keys = _session_key_aliases()
    # Turn-local only — bag keys linger across requests and falsely skip retry.
    if getattr(_turn_state, "clarify_called", False):
        return result
    if not peek_catalog_turn(*keys) and not peek_catalog_events(*keys):
        return result

    _log.warning(
        "clarify artifacts: forcing clarify retry keys=%s",
        keys[:6],
    )
    history = result.get("messages") if isinstance(result, dict) else None
    saved_delta = getattr(agent, "stream_delta_callback", None)
    _retry_guard.active = True
    try:
        if saved_delta is not None:
            agent.stream_delta_callback = None
        run_conversation(
            user_message=RETRY_USER_MESSAGE,
            conversation_history=history,
            stream_callback=None,
        )
    except Exception:
        _log.exception("clarify artifacts: retry failed")
    finally:
        if saved_delta is not None:
            agent.stream_delta_callback = saved_delta
        _retry_guard.active = False

    if getattr(_turn_state, "clarify_called", False) or peek_clarify(*keys):
        _log.warning("clarify artifacts: retry stored clarify")
    else:
        _log.warning("clarify artifacts: retry still missing clarify")
    return result


def wrap_agent_run_conversation(agent: Any) -> None:
    original = getattr(agent, "run_conversation", None)
    if not callable(original) or getattr(original, _RETRY_WRAPPED_ATTR, False):
        return

    @wraps(original)
    def run_conversation(*args, **kwargs):
        _turn_state.clarify_called = False
        result = original(*args, **kwargs)
        return maybe_retry_clarify(agent, result, original)

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
    """Give newly-created api_server agents the official callback attribute."""
    armed = False
    for candidate in vars(api_mod).values():
        if not isinstance(candidate, type):
            continue
        namespace = getattr(candidate, "__dict__", {})
        original_create_agent = namespace.get("_create_agent")
        if not callable(original_create_agent):
            continue
        if getattr(original_create_agent, _AGENT_PATCHED_ATTR, False):
            armed = True
            continue

        def make_create_agent(original):
            @wraps(original)
            def _create_agent(self, *args, **kwargs):
                agent = original(self, *args, **kwargs)
                agent.clarify_callback = api_server_clarify_callback
                wrap_agent_run_conversation(agent)
                return agent

            setattr(_create_agent, _AGENT_PATCHED_ATTR, True)
            return _create_agent

        _create_agent = make_create_agent(original_create_agent)
        setattr(candidate, "_create_agent", _create_agent)
        _log.warning(
            "clarify artifacts: wrapped %s._create_agent",
            candidate.__name__,
        )
        armed = True
    return armed


def _install_response_patch() -> bool:
    try:
        from aiohttp import web
        from gateway.platforms import api_server as api_mod
    except Exception:
        _log.debug("api_server unavailable; clarify artifacts idle")
        return False

    armed = _install_agent_patch(api_mod)

    if not _is_our_patch(getattr(web, "json_response", None)):
        original_json_response = web.json_response

        @wraps(original_json_response)
        def json_response(*args, **kwargs):
            body = args[0] if args else kwargs.get("data")
            if isinstance(body, dict) and body.get("object") == "chat.completion":
                headers = kwargs.get("headers")
                attach_clarify_to_payload(
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
                response_choices = data.get("choices") or []
                if response_choices and response_choices[0].get("finish_reason"):
                    attach_clarify_to_payload(data, *_session_key_aliases())
            if event is None:
                return original_frame(data)
            return original_frame(data, event=event)

        setattr(_sse_frame, _PATCHED_ATTR, True)
        api_mod._sse_frame = _sse_frame
        armed = True

    if armed:
        _shared_state()["armed"] = True
        return True

    sse_frame = getattr(api_mod, "_sse_frame", None)
    if _is_our_patch(getattr(web, "json_response", None)) and (
        sse_frame is None or _is_our_patch(sse_frame)
    ):
        # Agent patch may still be pending on a later import — keep trying.
        if _install_agent_patch(api_mod):
            _shared_state()["armed"] = True
            return True
        return bool(_shared_state().get("armed"))

    return False


def _ensure_armed(*_args, **_kwargs) -> None:
    """Re-arm after api_server finishes loading (plugins often register first)."""
    try:
        if _shared_state().get("armed") and _install_agent_patch_ok():
            return
        _install_response_patch()
    except Exception:
        _log.debug("clarify artifacts: ensure_armed failed", exc_info=True)


def _install_agent_patch_ok() -> bool:
    try:
        from gateway.platforms import api_server as api_mod
    except Exception:
        return False
    for candidate in vars(api_mod).values():
        if not isinstance(candidate, type):
            continue
        fn = getattr(candidate, "__dict__", {}).get("_create_agent")
        if callable(fn) and getattr(fn, _AGENT_PATCHED_ATTR, False):
            return True
    return False


def register(ctx) -> None:
    print("tcc-clarify-artifacts: register()", flush=True)
    try:
        ctx.register_hook("post_tool_call", on_post_tool_call)
    except Exception:
        _log.warning("clarify artifacts: post_tool_call hook unavailable", exc_info=True)
    try:
        if _install_response_patch():
            _log.warning("tcc-clarify-artifacts ready")
            print("tcc-clarify-artifacts: ready", flush=True)
        else:
            _log.warning("tcc-clarify-artifacts deferred arm (api_server not ready)")
            print("tcc-clarify-artifacts: deferred", flush=True)
    except Exception:
        _log.exception("clarify artifacts: registration failed")

    def _arm_loop() -> None:
        for _ in range(90):
            try:
                if _install_response_patch() and _install_agent_patch_ok():
                    _log.warning("tcc-clarify-artifacts armed after defer")
                    print("tcc-clarify-artifacts: armed after defer", flush=True)
                    return
            except Exception:
                _log.debug("clarify artifacts: defer arm tick failed", exc_info=True)
            time.sleep(2)
        _log.error("tcc-clarify-artifacts failed to arm within timeout")
        print("tcc-clarify-artifacts: failed to arm", flush=True)

    threading.Thread(target=_arm_loop, name="tcc-clarify-arm", daemon=True).start()
