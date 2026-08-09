"""Trusted principal injection for TCC MCP tool calls.

The principal is derived from the API-key-authenticated Hermes session context,
never from the model. Any model-supplied ``_hermes_principal`` is removed before
dispatch, so a prompt-injected value can't widen a caller's scope.

This is the security boundary for cross-store data access: without a principal
the TCC MCP server falls back to its fail-closed staff-id-0 scope (sees nothing).

Note that this is a SEPARATE axis from the per-user profile. The profile decides
whose *memory* and which *environment's MCP server* the turn uses; the principal
decides what that MCP server will show. Both must be right, and they come from
different places — the profile from the URL, the principal from the header.
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
# Matches BOTH the default profile's plain ``tcc-api`` and every per-environment
# name (``tcc-api-stg`` → ``mcp__tcc_api_stg__…``). Each environment needs its
# own server name because the MCP connection pool is keyed by name alone — see
# environments.mcp_server_name — so this pattern, not a fixed prefix, is what
# keeps the injector armed for all of them at once. Getting this wrong does not
# raise: calls simply go out unscoped and every answer comes back empty.
_TOOL_RE = re.compile(
    r"^mcp__" + re.sub(r"[^A-Za-z0-9_]", "_", MCP_SERVER_NAME) + r"(?:_[a-z0-9]+)?__"
)

# Kept for diagnostics/tests — the common stem of every name we match.
TOOL_PREFIX = "mcp__" + re.sub(r"[^A-Za-z0-9_]", "_", MCP_SERVER_NAME)

# staff-<id> | user-<id> | user-<id>-store-<id>
_PRINCIPAL_RE = re.compile(r"^(?:staff|user)-\d+(?:-store-\d+)?$")


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
        rewritten["_hermes_principal"] = principal
        _log.info("injected _hermes_principal=%s into %s", principal, tool_name)
    else:
        # The single most confusing failure in this system: the call succeeds,
        # the MCP server falls back to its staff-id-0 scope, and the user is
        # told "0 concerts" as though that were the answer. Never let that
        # happen silently.
        _log.warning(
            "NO principal resolved for %s — the MCP server will fail closed and "
            "answer as if the account owns nothing. The gateway session key was "
            "missing or malformed (expected staff-<id> / user-<id>-store-<id>).",
            tool_name,
        )

    return {"args": rewritten, "source": "tcc_mcp_config_principal_injector"}


def register(ctx) -> None:
    """Register trusted principal propagation before tool dispatch."""
    ctx.register_middleware("tool_request", inject_tcc_mcp_principal)
    _log.info("tcc-mcp-config: principal injector armed for %s", _TOOL_RE.pattern)
