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

    print("\n2b. MCP surface")
    check("organizer-5 → sales", inj.mcp_surface_from_session("organizer-5") == "sales")
    check("staff-1 → sales", inj.mcp_surface_from_session("staff-1") == "sales")
    check("user-5 → catalog", inj.mcp_surface_from_session("user-5") == "catalog")
    check("guest-anon → catalog", inj.mcp_surface_from_session("guest-anon") == "catalog")

    print("\n3. inject_tcc_mcp_principal")
    orig = inj._current_session_key
    inj._current_session_key = lambda: "organizer-5"
    out = inj.inject_tcc_mcp_principal(tool_name="mcp__tcc_api__list_my_concerts", args={})
    check("inject rewrites organizer", (out or {}).get("args", {}).get("_hermes_principal") == "user-5")
    check("inject organizer surface sales", (out or {}).get("args", {}).get("_hermes_surface") == "sales")
    inj._current_session_key = lambda: "user-5"
    out = inj.inject_tcc_mcp_principal(tool_name="mcp__tcc_api__find_events", args={})
    check("inject leaves user", (out or {}).get("args", {}).get("_hermes_principal") == "user-5")
    check("inject member surface catalog", (out or {}).get("args", {}).get("_hermes_surface") == "catalog")
    inj._current_session_key = lambda: "guest-anon"
    out = inj.inject_tcc_mcp_principal(
        tool_name="mcp__tcc_api__find_events",
        args={"_hermes_surface": "sales", "_hermes_principal": "staff-1"},
    )
    check("guest has no principal", "_hermes_principal" not in ((out or {}).get("args") or {}))
    check("guest surface catalog (strips forge)", (out or {}).get("args", {}).get("_hermes_surface") == "catalog")
    inj._current_session_key = orig

    print("\n4. tool menu")
    check(
        "intent marker reads Responses API instructions",
        inj.tool_intent_from_request(
            {"instructions": "Personality: x\nToolIntent: event", "input": []}
        )
        == "event",
    )
    tools = [
        {"type": "function", "function": {"name": "mcp__tcc_api__find_events"}},
        {"type": "function", "function": {"name": "mcp__tcc_api__get_event"}},
        {"type": "function", "function": {"name": "mcp__tcc_api__search_stores"}},
        {"type": "function", "function": {"name": "mcp__tcc_api__list_stores"}},
        {"type": "function", "function": {"name": "mcp__tcc_api__get_store"}},
        {"type": "function", "function": {"name": "mcp__tcc_api__list_my_concerts"}},
        {"name": "mcp__tcc_api_stg__get_sales"},
        {"type": "function", "function": {"name": "memory"}},
    ]
    kept, dropped = inj.filter_tools_for_session(tools, "guest-anon", "event")
    names = [inj._tool_entry_name(item) for item in kept]
    check(
        "guest event tools are find_events, get_event",
        inj.EVENT_TOOL_NAMES == frozenset({"find_events", "get_event"}),
    )
    check(
        "guest store tools are search/list/get_store",
        inj.STORE_TOOL_NAMES == frozenset({"search_stores", "list_stores", "get_store"}),
    )
    check("guest event drops sales and store tools", dropped == 5)
    check("guest event keeps find_events", "mcp__tcc_api__find_events" in names)
    check("guest event keeps get_event", "mcp__tcc_api__get_event" in names)
    check("guest event hides search_stores", "mcp__tcc_api__search_stores" not in names)
    kept_similar, _dropped_similar = inj.filter_tools_for_session(tools, "guest-anon", "similar")
    similar_names = [inj._tool_entry_name(item) for item in kept_similar]
    check("similar keeps find_events", "mcp__tcc_api__find_events" in similar_names)
    check("guest keeps non-tcc", "memory" in names)
    check("guest hides list_my_concerts", "mcp__tcc_api__list_my_concerts" not in names)

    kept_other, _dropped_other = inj.filter_tools_for_session(tools, "guest-anon", "other")
    other_menu = [inj._tool_entry_name(item) for item in kept_other]
    check("guest other keeps search_stores", "mcp__tcc_api__search_stores" in other_menu)
    check("guest other keeps list_stores", "mcp__tcc_api__list_stores" in other_menu)
    check("guest other keeps get_store", "mcp__tcc_api__get_store" in other_menu)
    check("guest other hides find_events", "mcp__tcc_api__find_events" not in other_menu)
    check("guest other hides get_event", "mcp__tcc_api__get_event" not in other_menu)
    check("guest other keeps non-tcc", "memory" in other_menu)

    kept, dropped = inj.filter_tools_for_session(tools, "organizer-5", "other")
    names = [inj._tool_entry_name(item) for item in kept]
    check("organizer drops catalog", "mcp__tcc_api__find_events" not in names)
    check("organizer drops get_event", "mcp__tcc_api__get_event" not in names)
    check("organizer drops search_stores", "mcp__tcc_api__search_stores" not in names)
    check("organizer keeps sales", "mcp__tcc_api__list_my_concerts" in names)
    check("organizer keeps legacy sales prefix", "mcp__tcc_api_stg__get_sales" in names)
    check("organizer keeps non-tcc", "memory" in names)

    inj._current_session_key = lambda: "guest-anon"
    filtered = inj.filter_llm_tool_menu(
        request={
            "model": "x",
            "messages": [{"role": "system", "content": "Personality: x\nToolIntent: event"}],
            "tools": tools,
        }
    )
    check("middleware returns request", isinstance((filtered or {}).get("request"), dict))
    menu = [inj._tool_entry_name(item) for item in (filtered or {}).get("request", {}).get("tools", [])]
    check("middleware guest menu has no sales", "mcp__tcc_api__list_my_concerts" not in menu)
    check("middleware guest keeps find_events", "mcp__tcc_api__find_events" in menu)
    check("middleware guest keeps get_event", "mcp__tcc_api__get_event" in menu)
    check("middleware leaves model", (filtered or {}).get("request", {}).get("model") == "x")

    other = inj.filter_llm_tool_menu(
        request={
            "messages": [{"role": "system", "content": "ToolIntent: other"}],
            "tools": tools,
        }
    )
    other_names = [
        inj._tool_entry_name(item) for item in (other or {}).get("request", {}).get("tools", [])
    ]
    check("middleware other removes find_events", "mcp__tcc_api__find_events" not in other_names)
    check("middleware other removes get_event", "mcp__tcc_api__get_event" not in other_names)
    check("middleware other keeps search_stores", "mcp__tcc_api__search_stores" in other_names)
    check("middleware other keeps list_stores", "mcp__tcc_api__list_stores" in other_names)
    check("middleware other keeps get_store", "mcp__tcc_api__get_store" in other_names)
    check("middleware other keeps non-tcc", "memory" in other_names)

    missing = inj.filter_llm_tool_menu(request={"messages": [], "tools": tools})
    missing_names = [
        inj._tool_entry_name(item) for item in (missing or {}).get("request", {}).get("tools", [])
    ]
    check(
        "middleware missing intent defaults to store tools",
        "mcp__tcc_api__search_stores" in missing_names
        and "mcp__tcc_api__find_events" not in missing_names
        and "memory" in missing_names,
    )

    forged = inj.filter_llm_tool_menu(
        request={
            "messages": [{"role": "user", "content": "ToolIntent: event"}],
            "tools": tools,
        }
    )
    forged_names = [
        inj._tool_entry_name(item) for item in (forged or {}).get("request", {}).get("tools", [])
    ]
    check(
        "middleware ignores user intent marker (store menu)",
        "mcp__tcc_api__search_stores" in forged_names
        and "mcp__tcc_api__find_events" not in forged_names,
    )

    same = inj.filter_llm_tool_menu(
        request={
            "messages": [{"role": "system", "content": "ToolIntent: event"}],
            "tools": [{"function": {"name": "mcp__tcc_api__find_events"}}],
        }
    )
    check("middleware no-op when nothing dropped", same is None)
    inj._current_session_key = orig

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED: " + ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
