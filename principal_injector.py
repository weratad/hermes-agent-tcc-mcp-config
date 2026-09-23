"""Trusted principal + surface injection for TCC MCP tool calls.

The principal and surface are derived from the API-key-authenticated Hermes
session context, never from the model. Any model-supplied ``_hermes_principal``
or ``_hermes_surface`` is removed before dispatch.

Surfaces (same POST /mcp):
    staff-* / organizer-*  → sales
    user-* / guest-anon / anything else → catalog

This is the security boundary that keeps AI ASK members off organizer sales
tools even though Hermes discovers the full tool list at gateway startup.

Discovery stays process-global. Before each model call, ``llm_request``
middleware drops the other surface's TCC tools so the model only sees the
menu for this session. ``tool_request`` still overwrites principal and
surface on the call, so a forged tool name cannot cross surfaces.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .environments import MCP_SERVER_NAME

_log = logging.getLogger("hermes.plugin.tcc-mcp-config.principal-injector")

# Hermes builds tool names as mcp__<server>__<tool> and sanitizes the server
# component with re.sub(r"[^A-Za-z0-9_]", "_", ...) — see tools/mcp_tool.py
# (sanitize_mcp_name_component / mcp_prefixed_tool_name).
#
# Matches ``tcc-api`` (``mcp__tcc_api__…``) plus leftover per-environment names
# from older plugin versions (``tcc-api-stg`` → ``mcp__tcc_api_stg__…``) until
# the gateway restarts and reconnects under the single server name. Getting
# this wrong does not raise: calls simply go out unscoped and every answer
# comes back empty.
_TOOL_RE = re.compile(
    r"^mcp__" + re.sub(r"[^A-Za-z0-9_]", "_", MCP_SERVER_NAME) + r"(?:_[a-z0-9]+)?__"
)

# Kept for diagnostics/tests — the common stem of every name we match.
TOOL_PREFIX = "mcp__" + re.sub(r"[^A-Za-z0-9_]", "_", MCP_SERVER_NAME)

# staff-<id> | user-<id> | organizer-<id> | same with -store-<id>
_PRINCIPAL_RE = re.compile(r"^(?:staff|user|organizer)-\d+(?:-store-\d+)?$")
_TOOL_INTENT_RE = re.compile(r"(?:^|\n)ToolIntent:\s*(event|similar|other)\s*(?:\n|$)")

# Guest/member menus — split by ToolIntent so store asks do not fall back to find_events.
EVENT_TOOL_NAMES = frozenset({
    "find_events",
    "get_event",
})
STORE_TOOL_NAMES = frozenset({
    "search_stores",
    "list_stores",
    "get_store",
})
# Union kept for diagnostics/tests that still reference the old name.
CATALOG_TOOL_NAMES = EVENT_TOOL_NAMES | STORE_TOOL_NAMES
SALES_TOOL_NAMES = frozenset({
    "list_my_concerts",
    "get_sales_overview", "get_ticket_sales", "get_sales", "get_top_buyers",
    "get_buyer_insights", "get_order_status_summary", "get_payment_channels",
    "get_ticket_inventory", "get_checkin_summary", "get_order", "get_ticket",
    "get_transfers", "get_refunds", "get_resells",
    "simulate_fee",
    "get_event_schedule",
    "get_reserve_overview", "get_reserve_bookings", "get_reserve_agents",
    "get_reserve_order_status",
})


def mcp_principal_from_session(session_key: str) -> str:
    """Map a Hermes session key to the MCP principal tcc-api understands.

    Organizer chat uses session ``organizer-<id>`` so memory stays separate from
    AI ASK's ``user-<id>``, but MCP sales scope is still the TCC user.
    """
    if session_key.startswith("organizer-"):
        return "user-" + session_key[len("organizer-") :]
    return session_key


def mcp_surface_from_session(session_key: str) -> str:
    """staff/organizer may call sales tools; members and guests may call catalog tools."""
    key = str(session_key or "").strip()
    if key.startswith("staff-") or key.startswith("organizer-"):
        return "sales"
    return "catalog"


def _current_session_key() -> str:
    """Return the API-key-authenticated gateway session key (unvalidated)."""
    try:
        from tools.approval import get_current_session_key

        session_key = get_current_session_key(default="")
    except Exception:
        _log.exception("Could not resolve the trusted Hermes session key")
        return ""
    return str(session_key or "").strip()


def _current_trusted_principal() -> str:
    """Return the validated API-key-authenticated gateway session key."""
    normalized = _current_session_key()
    if not normalized:
        return ""
    if not _PRINCIPAL_RE.fullmatch(normalized):
        _log.warning("Ignoring malformed session key (not a TCC principal)")
        return ""
    return normalized


def inject_tcc_mcp_principal(
    *,
    tool_name: str,
    args: Dict[str, Any],
    **_: Any,
) -> Optional[Dict[str, Any]]:
    """Rewrite only TCC MCP calls with a server-trusted principal and surface."""
    if not _TOOL_RE.match(str(tool_name or "")):
        return None

    rewritten = dict(args or {})
    rewritten.pop("_hermes_principal", None)
    rewritten.pop("_hermes_surface", None)

    session = _current_session_key()
    surface = mcp_surface_from_session(session)
    rewritten["_hermes_surface"] = surface

    if session and _PRINCIPAL_RE.fullmatch(session):
        mcp_principal = mcp_principal_from_session(session)
        rewritten["_hermes_principal"] = mcp_principal
        _log.info(
            "injected _hermes_principal=%s _hermes_surface=%s into %s",
            mcp_principal,
            surface,
            tool_name,
        )
    elif surface == "sales":
        # Sales tools fail-closed at staff-id-0 without a principal. Catalog
        # guests (guest-anon) are expected to have no principal.
        _log.warning(
            "NO principal resolved for %s — the MCP server will fail closed and "
            "answer as if the account owns nothing. The gateway session key was "
            "missing or malformed (expected staff-<id> / user-<id> / organizer-<id>"
            "[-store-<id>]).",
            tool_name,
        )

    return {"args": rewritten, "source": "tcc_mcp_config_principal_injector"}


def tcc_tool_leaf(tool_name: str) -> Optional[str]:
    """Return the catalog/sales name, or None when this is not a TCC MCP tool."""
    name = str(tool_name or "")
    match = _TOOL_RE.match(name)
    if not match:
        return None
    leaf = name[match.end():]
    return leaf or None


def _tool_entry_name(entry: Any) -> str:
    """OpenAI ``function.name``, Responses ``name``, or a bare Hermes tool dict."""
    if not isinstance(entry, dict):
        return ""
    function = entry.get("function")
    if isinstance(function, dict) and function.get("name"):
        return str(function["name"])
    return str(entry.get("name") or "")


def tool_intent_from_request(request: Dict[str, Any]) -> str:
    """Read the server-generated marker from Chat or Responses system input."""
    instructions = request.get("instructions")
    if isinstance(instructions, str):
        match = _TOOL_INTENT_RE.search(instructions)
        if match:
            return match.group(1)

    messages = request.get("messages")
    if not isinstance(messages, list):
        return "other"
    intent = "other"
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "system":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        match = _TOOL_INTENT_RE.search(content)
        if match:
            intent = match.group(1)
    return intent


def filter_tools_for_session(
    tools: List[Any],
    session_key: str,
    tool_intent: str = "other",
) -> tuple:
    """Drop TCC tools that do not belong to this session. Other tools stay."""
    surface = mcp_surface_from_session(session_key)
    if surface == "sales":
        allowed = SALES_TOOL_NAMES
    elif tool_intent in ("event", "similar"):
        allowed = EVENT_TOOL_NAMES
    elif tool_intent == "other":
        allowed = STORE_TOOL_NAMES
    else:
        allowed = frozenset()
    kept: List[Any] = []
    dropped = 0
    for entry in tools:
        leaf = tcc_tool_leaf(_tool_entry_name(entry))
        if leaf is not None and leaf not in allowed:
            dropped += 1
            continue
        kept.append(entry)
    return kept, dropped


def filter_llm_tool_menu(
    *,
    request: Dict[str, Any],
    **_: Any,
) -> Optional[Dict[str, Any]]:
    """Hide the other surface's TCC tools before the provider sees the request."""
    if not isinstance(request, dict):
        return None
    tools = request.get("tools")
    if not isinstance(tools, list):
        return None

    session = _current_session_key()
    tool_intent = tool_intent_from_request(request)
    kept, dropped = filter_tools_for_session(tools, session, tool_intent)

    try:
        from . import ai_ask_profile as _aap
    except ImportError:
        import importlib.util
        from pathlib import Path as _P
        _spec = importlib.util.spec_from_file_location(
            "_tcc_ai_ask_profile", _P(__file__).resolve().parent / "ai_ask_profile.py"
        )
        _aap = importlib.util.module_from_spec(_spec)
        assert _spec and _spec.loader
        _spec.loader.exec_module(_aap)
    AI_ASK_NATIVE_TOOLS = _aap.AI_ASK_NATIVE_TOOLS
    is_ai_ask_user_profile = _aap.is_ai_ask_user_profile

    if not is_ai_ask_user_profile():
        narrowed: List[Any] = []
        for entry in kept:
            leaf = str(_tool_entry_name(entry) or "").replace("__", ".").split(".")[-1]
            if leaf in AI_ASK_NATIVE_TOOLS:
                dropped += 1
                continue
            narrowed.append(entry)
        kept = narrowed

    if dropped == 0:
        return None

    surface = mcp_surface_from_session(session)
    next_request = dict(request)
    next_request["tools"] = kept
    _log.info(
        "tcc tool menu surface=%s intent=%s kept=%s dropped=%s",
        surface,
        tool_intent,
        len(kept),
        dropped,
    )
    return {"request": next_request, "source": "tcc_mcp_config_tool_menu"}


def register(ctx) -> None:
    """Register trusted principal propagation and the per-session tool menu."""
    ctx.register_middleware("tool_request", inject_tcc_mcp_principal)
    ctx.register_middleware("llm_request", filter_llm_tool_menu)
    _log.info("tcc-mcp-config: principal injector armed for %s", _TOOL_RE.pattern)
