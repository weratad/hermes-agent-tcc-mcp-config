"""Principal session-key validation + organizer→user MCP rewrite.

Run: python3 tests/test_principal.py /path/to/tcc-mcp-config
Does not need a running Hermes gateway.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path



def main():
    PLUGIN_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent

    pkg_name = "tcc_mcp_config"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(PLUGIN_DIR)]
    sys.modules[pkg_name] = pkg

    env_spec = importlib.util.spec_from_file_location(f"{pkg_name}.environments", PLUGIN_DIR / "environments.py")
    env_mod = importlib.util.module_from_spec(env_spec)
    sys.modules[f"{pkg_name}.environments"] = env_mod
    assert env_spec.loader is not None
    env_spec.loader.exec_module(env_mod)

    inj_spec = importlib.util.spec_from_file_location(
        f"{pkg_name}.principal_injector", PLUGIN_DIR / "principal_injector.py"
    )
    inj = importlib.util.module_from_spec(inj_spec)
    sys.modules[f"{pkg_name}.principal_injector"] = inj
    assert inj_spec.loader is not None
    inj_spec.loader.exec_module(inj)

    PASS, FAIL = [], []


    def check(label, condition, detail=""):
        (PASS if condition else FAIL).append(label)
        print(("  ok   " if condition else "  FAIL ") + label + ((" — " + str(detail)) if detail and not condition else ""))


    print("\n1. session key regex")
    check("accepts organizer-5", bool(inj._PRINCIPAL_RE.fullmatch("organizer-5")))
    check("accepts organizer-5-store-9", bool(inj._PRINCIPAL_RE.fullmatch("organizer-5-store-9")))
    check("accepts user-5", bool(inj._PRINCIPAL_RE.fullmatch("user-5")))
    check("accepts staff-1", bool(inj._PRINCIPAL_RE.fullmatch("staff-1")))
    check("rejects guest-anon", not inj._PRINCIPAL_RE.fullmatch("guest-anon"))

    print("\n2. MCP rewrite")
    check("organizer-5 -> user-5", inj.mcp_principal_from_session("organizer-5") == "user-5")
    check(
        "organizer-5-store-9 -> user-5-store-9",
        inj.mcp_principal_from_session("organizer-5-store-9") == "user-5-store-9",
    )
    check("user-5 unchanged", inj.mcp_principal_from_session("user-5") == "user-5")
    check("staff-1 unchanged", inj.mcp_principal_from_session("staff-1") == "staff-1")

    print("\n3. inject_tcc_mcp_principal")
    orig = inj._current_trusted_principal
    inj._current_trusted_principal = lambda: "organizer-5"
    out = inj.inject_tcc_mcp_principal(tool_name="mcp__tcc_api__list_my_concerts", args={})
    check("inject rewrites organizer", (out or {}).get("args", {}).get("_hermes_principal") == "user-5")
    inj._current_trusted_principal = lambda: "user-5"
    out = inj.inject_tcc_mcp_principal(tool_name="mcp__tcc_api__list_my_concerts", args={})
    check("inject leaves user", (out or {}).get("args", {}).get("_hermes_principal") == "user-5")
    inj._current_trusted_principal = orig

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED: " + ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
