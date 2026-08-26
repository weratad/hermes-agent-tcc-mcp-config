"""Verify the provisioner's monkey-patches bind to the real Hermes classes.

Loads the plugin exactly the way PluginManager does (namespace package with
submodule_search_locations), then drives ApiServerPlatform._resolve_request_profile
with a stub request. Uses a throwaway HERMES_HOME.
"""

import asyncio
import importlib.util
import shutil
import sys
import tempfile
import types
from pathlib import Path



def main():
    PLUGIN_DIR = Path(sys.argv[1])

    SANDBOX = Path(tempfile.mkdtemp(prefix="tcc-patch-home-"))
    (SANDBOX / "profiles").mkdir(parents=True)
    (SANDBOX / "config.yaml").write_text("model:\n  default: gpt-5.4-mini\n", encoding="utf-8")
    (SANDBOX / ".env").write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")

    # ── load the plugin as hermes does ───────────────────────────────────────
    NS = "hermes_plugins"
    if NS not in sys.modules:
        ns_pkg = types.ModuleType(NS)
        ns_pkg.__path__ = []
        sys.modules[NS] = ns_pkg

    module_name = f"{NS}.tcc_mcp_config"
    spec = importlib.util.spec_from_file_location(
        module_name, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
    )
    plugin = importlib.util.module_from_spec(spec)
    plugin.__package__ = module_name
    plugin.__path__ = [str(PLUGIN_DIR)]
    sys.modules[module_name] = plugin
    spec.loader.exec_module(plugin)

    envs = plugin.environments
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _sandbox
    _sandbox.pin(envs, SANDBOX)

    PASS, FAIL = [], []


    def check(label, condition, detail=""):
        (PASS if condition else FAIL).append(label)
        print(("  ok   " if condition else "  FAIL ") + label + ((" — " + str(detail)) if detail and not condition else ""))


    print("\n1. relative imports resolved")
    check("environments loaded", hasattr(plugin, "environments"))
    check("provisioner loaded", hasattr(plugin, "provisioner"))
    inj = plugin.principal_injector
    for name in ("mcp__tcc_api__list_my_concerts", "mcp__tcc_api_stg__list_my_concerts",
                 "mcp__tcc_api_prod__get_sales", "mcp__tcc_api_local__get_sales"):
        check(f"injector matches {name}", bool(inj._TOOL_RE.match(name)))
    for name in ("mcp__other__list", "list_my_concerts", "mcp__tcc_apix__t", "mcp__tcc__api__t"):
        check(f"injector ignores {name}", not inj._TOOL_RE.match(name))

    print("\n1b. organizer session keys rewrite to user for MCP")
    check("accepts organizer-5", bool(inj._PRINCIPAL_RE.fullmatch("organizer-5")))
    check("accepts organizer-5-store-9", bool(inj._PRINCIPAL_RE.fullmatch("organizer-5-store-9")))
    check("user-5 still matches", bool(inj._PRINCIPAL_RE.fullmatch("user-5")))
    check("rejects guest-anon", not inj._PRINCIPAL_RE.fullmatch("guest-anon"))
    check("organizer-5 -> user-5", inj.mcp_principal_from_session("organizer-5") == "user-5")
    check(
        "organizer-5-store-9 -> user-5-store-9",
        inj.mcp_principal_from_session("organizer-5-store-9") == "user-5-store-9",
    )
    check("user-5 unchanged", inj.mcp_principal_from_session("user-5") == "user-5")
    check("staff-1 unchanged", inj.mcp_principal_from_session("staff-1") == "staff-1")
    check("organizer-5 surface sales", inj.mcp_surface_from_session("organizer-5") == "sales")
    check("staff-1 surface sales", inj.mcp_surface_from_session("staff-1") == "sales")
    check("user-5 surface catalog", inj.mcp_surface_from_session("user-5") == "catalog")
    check("guest-anon surface catalog", inj.mcp_surface_from_session("guest-anon") == "catalog")
    _orig_session = inj._current_session_key
    inj._current_session_key = lambda: "organizer-5"
    _out = inj.inject_tcc_mcp_principal(tool_name="mcp__tcc_api__list_my_concerts", args={})
    check("inject rewrites organizer", (_out or {}).get("args", {}).get("_hermes_principal") == "user-5")
    check("inject organizer surface sales", (_out or {}).get("args", {}).get("_hermes_surface") == "sales")
    inj._current_session_key = lambda: "user-5"
    _out = inj.inject_tcc_mcp_principal(tool_name="mcp__tcc_api__search_events", args={})
    check("inject leaves user", (_out or {}).get("args", {}).get("_hermes_principal") == "user-5")
    check("inject member surface catalog", (_out or {}).get("args", {}).get("_hermes_surface") == "catalog")
    inj._current_session_key = lambda: "guest-anon"
    _out = inj.inject_tcc_mcp_principal(tool_name="mcp__tcc_api__search_events", args={"_hermes_surface": "sales"})
    check("guest has no principal", "_hermes_principal" not in ((_out or {}).get("args") or {}))
    check("guest surface catalog (strips forge)", (_out or {}).get("args", {}).get("_hermes_surface") == "catalog")
    inj._current_session_key = _orig_session

    print("\n2. patches install onto the real class")
    from gateway.platforms.api_server import APIServerAdapter as ApiServerPlatform, _PROFILE_REJECTED

    before_resolve = ApiServerPlatform._resolve_request_profile
    before_routes = ApiServerPlatform._http_route_table
    plugin.provisioner.install()
    check("_resolve_request_profile wrapped", ApiServerPlatform._resolve_request_profile is not before_resolve)
    check("_http_route_table wrapped", ApiServerPlatform._http_route_table is not before_routes)

    again_resolve = ApiServerPlatform._resolve_request_profile
    plugin.provisioner.install()
    check("install() is idempotent", ApiServerPlatform._resolve_request_profile is again_resolve)

    print("\n3. ensure route is registered")


    class StubPlatform(ApiServerPlatform):
        def __init__(self):  # bypass the real __init__
            pass


    routes = StubPlatform()._http_route_table()
    paths = [(method, path) for method, path, _ in routes]
    check("POST ensure path present", ("POST", plugin.provisioner.ENSURE_PATH) in paths, paths[-3:])
    check("original routes still there", ("POST", "/v1/chat/completions") in paths)


    # ── stub request/runner so _resolve_request_profile can run ──────────────
    class StubRequest:
        def __init__(self, profile, bearer=None):
            self.match_info = {"profile": profile}
            self.headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}


    class StubConfig:
        multiplex_profiles = True


    class StubRunner:
        config = StubConfig()


    platform = StubPlatform()
    platform.gateway_runner = StubRunner()

    # hermes' own profiles_to_serve must look at the sandbox, not /root/.hermes
    import hermes_cli.profiles as hp

    hp._get_default_hermes_home = lambda: SANDBOX
    hp._get_profiles_root = lambda: SANDBOX / "profiles"
    hp.get_active_profile_name = lambda: "default"
    hp.get_profile_dir = lambda name: SANDBOX / "profiles" / name

    GOOD_KEY = "stg-gateway-key-0123456789abcdef"
    envs.save_settings(url="https://api.tcc-stg.com/mcp", mcp_api_key="stg-mcp", gateway_key=GOOD_KEY)

    print("\n4. auto-provisioning on an unknown profile")
    name = "user-2520153"
    check("profile absent to begin with", not envs.profile_dir(name).exists())
    result = platform._resolve_request_profile(StubRequest(name, GOOD_KEY))
    check("resolves after provisioning", result == name, result)
    check("directory created", envs.profile_dir(name).is_dir())

    result = platform._resolve_request_profile(StubRequest(name, GOOD_KEY))
    check("second request resolves without re-creating", result == name, result)

    print("\n5. the pre-auth window is closed")
    result = platform._resolve_request_profile(StubRequest("user-777"))
    check("no bearer -> rejected", result is _PROFILE_REJECTED)
    check("no bearer -> nothing written", not envs.profile_dir("user-777").exists())

    result = platform._resolve_request_profile(StubRequest("user-778", "wrong-key-but-long-enough"))
    check("wrong bearer -> rejected", result is _PROFILE_REJECTED)
    check("wrong bearer -> nothing written", not envs.profile_dir("user-778").exists())

    result = platform._resolve_request_profile(StubRequest("staff-689", GOOD_KEY))
    check("same key can create a staff profile", result == "staff-689", result)

    print("\n6. non-TCC names fall through to hermes' own 404")
    for junk in ("default", "some-other-profile", "../etc", "stg-user-1"):
        result = platform._resolve_request_profile(StubRequest(junk))
        check(f"{junk!r} -> untouched", result is _PROFILE_REJECTED or result == junk)
    check(
        "no junk dirs created",
        sorted(p.name for p in (SANDBOX / "profiles").iterdir()) == ["staff-689", "user-2520153"],
    )

    print("\n7. ensure endpoint handler")
    handler = [h for m, p, h in routes if p == plugin.provisioner.ENSURE_PATH][0]


    class StubJsonRequest(StubRequest):
        def __init__(self, payload, bearer=None):
            super().__init__("", bearer)
            self._payload = payload

        async def json(self):
            return self._payload


    def run(payload, bearer=None):
        return asyncio.get_event_loop().run_until_complete(handler(StubJsonRequest(payload, bearer)))


    asyncio.set_event_loop(asyncio.new_event_loop())
    res = run({"environment": "staging", "type": "staff", "id": 690}, GOOD_KEY)
    check("ignores environment field", res.status == 200, res.status)
    check("staff profile created without env prefix", envs.profile_dir("staff-690").is_dir())
    res = run({"environment": "staging", "type": "staff", "id": 690}, GOOD_KEY)
    check("repeat call is 200", res.status == 200, res.status)
    res = run({"environment": "qa", "type": "user", "id": 42}, GOOD_KEY)
    check("unknown env still succeeds", res.status == 200, res.status)
    check("user-42 created", envs.profile_dir("user-42").is_dir())
    res = run({"environment": "staging", "type": "staff", "id": 691}, "bad")
    check("bad bearer -> 401", res.status == 401, res.status)
    check("unauthorized did not create a dir", not envs.profile_dir("staff-691").exists())
    res = run({"environment": "staging", "type": "root", "id": 1}, GOOD_KEY)
    check("bad principal type -> 400", res.status == 400, res.status)

    shutil.rmtree(SANDBOX, ignore_errors=True)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
