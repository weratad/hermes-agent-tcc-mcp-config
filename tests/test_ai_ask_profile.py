"""AI Ask profile gate — only user-* multiplex profiles."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def main() -> None:
    plugin = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("ai_ask_profile", plugin / "ai_ask_profile.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.is_user_profile_name("user-1")
    assert mod.is_user_profile_name("user-2520153")
    assert mod.is_user_profile_name("user-7-store-9")
    assert not mod.is_user_profile_name("staff-3")
    assert not mod.is_user_profile_name("organizer-5")
    assert not mod.is_user_profile_name("default")
    assert not mod.is_user_profile_name("guest-anon")
    assert "present_layout" in mod.AI_ASK_NATIVE_TOOLS
    assert "clarify" in mod.AI_ASK_NATIVE_TOOLS

    tools_pkg = types.ModuleType("tools")
    approval = types.ModuleType("tools.approval")

    def get_current_session_key(default=""):
        return "user-42"

    approval.get_current_session_key = get_current_session_key
    sys.modules["tools"] = tools_pkg
    sys.modules["tools.approval"] = approval

    mod.active_profile_name = lambda: ""
    assert mod.is_ai_ask_user_profile() is True

    mod.active_profile_name = lambda: "staff-3"
    assert mod.is_ai_ask_user_profile() is False

    mod.active_profile_name = lambda: "user-1"
    assert mod.is_ai_ask_user_profile() is True
    print("ai_ask_profile ok")


if __name__ == "__main__":
    main()
