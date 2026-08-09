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
envs.default_home = lambda: SANDBOX

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
envs.save_settings("stg", url="https://api.tcc-stg.com/mcp", mcp_api_key="stg-mcp", gateway_key=GOOD_KEY)
envs.save_settings("prod", url="https://api.theconcert.com/mcp", mcp_api_key="p", gateway_key="prod-gateway-key-0123456789")

print("\n4. auto-provisioning on an unknown profile")
name = "stg-user-2520153"
check("profile absent to begin with", not envs.profile_dir(name).exists())
result = platform._resolve_request_profile(StubRequest(name, GOOD_KEY))
check("resolves after provisioning", result == name, result)
check("directory created", envs.profile_dir(name).is_dir())

result = platform._resolve_request_profile(StubRequest(name, GOOD_KEY))
check("second request resolves without re-creating", result == name, result)

print("\n5. the pre-auth window is closed")
result = platform._resolve_request_profile(StubRequest("stg-user-777"))
check("no bearer -> rejected", result is _PROFILE_REJECTED)
check("no bearer -> nothing written", not envs.profile_dir("stg-user-777").exists())

result = platform._resolve_request_profile(StubRequest("stg-user-778", "wrong-key-but-long-enough"))
check("wrong bearer -> rejected", result is _PROFILE_REJECTED)
check("wrong bearer -> nothing written", not envs.profile_dir("stg-user-778").exists())

result = platform._resolve_request_profile(StubRequest("prod-user-1", GOOD_KEY))
check("staging key cannot reach prod", result is _PROFILE_REJECTED)
check("prod profile not created", not envs.profile_dir("prod-user-1").exists())

result = platform._resolve_request_profile(StubRequest("prod-user-1", "prod-gateway-key-0123456789"))
check("prod key does create prod", result == "prod-user-1", result)

print("\n6. non-TCC names fall through to hermes' own 404")
for junk in ("default", "some-other-profile", "../etc"):
    result = platform._resolve_request_profile(StubRequest(junk))
    check(f"{junk!r} -> untouched", result is _PROFILE_REJECTED or result == junk)
check("no junk dirs created", sorted(p.name for p in (SANDBOX / "profiles").iterdir()) == ["prod-user-1", "stg-user-2520153"])

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
res = run({"environment": "staging", "type": "staff", "id": 689}, GOOD_KEY)
check("creates from long env name", res.status == 200, res.status)
check("staff profile created", envs.profile_dir("stg-staff-689").is_dir())
res = run({"environment": "staging", "type": "staff", "id": 689}, GOOD_KEY)
check("repeat call is 200", res.status == 200, res.status)
res = run({"environment": "staging", "type": "staff", "id": 690}, "bad")
check("bad bearer -> 401", res.status == 401, res.status)
res = run({"environment": "qa", "type": "staff", "id": 1}, GOOD_KEY)
check("unknown env -> 400", res.status == 400, res.status)
res = run({"environment": "staging", "type": "root", "id": 1}, GOOD_KEY)
check("bad principal type -> 400", res.status == 400, res.status)

shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
