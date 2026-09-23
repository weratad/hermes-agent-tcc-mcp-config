"""Gate AI Ask artifact tools to multiplex profiles named ``user-*`` only.

Staff / organizer sales chat must not get ``present_layout`` / ``clarify`` /
catalog attach. Guest AI Ask uses ``user-1`` (or another ``user-<id>``) so it
stays in scope.
"""

from __future__ import annotations

import re
from pathlib import Path

# Same shape as environments.PROFILE_RE user branch (no staff / organizer).
USER_PROFILE_RE = re.compile(r"^user-[0-9]{1,12}(?:-store-[0-9]{1,12})?$")

AI_ASK_NATIVE_TOOLS = frozenset({"present_layout", "clarify"})


def is_user_profile_name(name: str) -> bool:
    return bool(USER_PROFILE_RE.match(str(name or "").strip()))


def active_profile_name() -> str:
    """Best-effort current multiplex profile id (``user-123``, ``staff-3``, …)."""
    try:
        from hermes_cli import profiles as hp

        name = str(getattr(hp, "get_active_profile_name", lambda: "")() or "").strip()
        if name and name != "default":
            return name
    except Exception:
        pass
    try:
        from hermes_constants import get_hermes_home

        home = Path(get_hermes_home())
        # Under /p/<name>/ Hermes scopes HERMES_HOME to profiles/<name>.
        if home.parent.name == "profiles" and home.name:
            return home.name
    except Exception:
        pass
    return ""


def is_ai_ask_user_profile() -> bool:
    name = active_profile_name()
    if name:
        # Explicit non-user profile (staff/organizer) must stay gated off even
        # if the session key looks like user-*.
        return is_user_profile_name(name)
    # Fallback only when multiplex name is missing.
    try:
        from tools.approval import get_current_session_key

        key = str(get_current_session_key(default="") or "").strip()
        if is_user_profile_name(key):
            return True
    except Exception:
        pass
    return False
