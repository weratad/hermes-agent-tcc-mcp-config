"""Trusted principal injection for TCC MCP tool calls.

The principal is derived from the API-key-authenticated Hermes session context,
never from the model. Any model-supplied ``_hermes_principal`` is removed before
dispatch, so a prompt-injected value can't widen a caller's scope.

This is the security boundary for cross-store data access: without a principal
the TCC MCP server falls back to its fail-closed staff-id-0 scope (sees nothing).

Note that this is a SEPARATE axis from the per-user profile. The profile decides
whose *memory* the turn uses; the principal decides what the MCP server will
show. Both must be right, and they come from different places — the profile
from the URL, the principal from the header.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

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


def mcp_principal_from_session(session_key: str) -> str:
    """Map a Hermes session key to the MCP principal tcc-api understands.

    Organizer chat uses session ``organizer-<id>`` so memory stays separate from
    AI ASK's ``user-<id>``, but MCP sales scope is still the TCC user.
    """
    if session_key.startswith("organizer-"):
        return "user-" + session_key[len("organizer-") :]
    return session_key


def _current_trusted_principal() -> str:
    """Return the validated API-key-authenticated gateway session key."""
    try:
        from tools.approval import get_current_session_key

        session_key = get_current_session_key(default="")
    except Exception:
        _log.exception("Could not resolve the trusted Hermes session key")
        return ""

    normalized = str(session_key or "").strip()
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
    """Rewrite only TCC MCP calls with a server-trusted principal."""
    if not _TOOL_RE.match(str(tool_name or "")):
        return None

    rewritten = dict(args or {})
    rewritten.pop("_hermes_principal", None)

    principal = _current_trusted_principal()
    if principal:
        mcp_principal = mcp_principal_from_session(principal)
        rewritten["_hermes_principal"] = mcp_principal
        _log.info("injected _hermes_principal=%s into %s", mcp_principal, tool_name)
    else:
        # The single most confusing failure in this system: the call succeeds,
        # the MCP server falls back to its staff-id-0 scope, and the user is
        # told "0 concerts" as though that were the answer. Never let that
        # happen silently.
        _log.warning(
            "NO principal resolved for %s — the MCP server will fail closed and "
            "answer as if the account owns nothing. The gateway session key was "
            "missing or malformed (expected staff-<id> / user-<id> / organizer-<id>"
            "[-store-<id>]).",
            tool_name,
        )

    return {"args": rewritten, "source": "tcc_mcp_config_principal_injector"}


def register(ctx) -> None:
    """Register trusted principal propagation before tool dispatch."""
    ctx.register_middleware("tool_request", inject_tcc_mcp_principal)
    _log.info("tcc-mcp-config: principal injector armed for %s", _TOOL_RE.pattern)
