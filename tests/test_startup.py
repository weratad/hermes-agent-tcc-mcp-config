"""Prove a provisioned profile cannot break gateway startup.

At startup the multiplexer loads EVERY profile's config
(_start_secondary_profile_adapters). Two of its failure modes are fatal or
noisy at scale, so both are checked here against a real generated profile:

  * MultiplexConfigError  -> re-raised, aborts the whole gateway
  * SecondaryPortBindingConfigError -> profile skipped with a warning

With hundreds of user profiles either one would be a bad day, and neither is
visible until a restart.
"""

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

PLUGIN_DIR = Path(sys.argv[1])
SANDBOX = Path(tempfile.mkdtemp(prefix="tcc-startup-home-"))
(SANDBOX / "profiles").mkdir(parents=True)
(SANDBOX / "config.yaml").write_text("model:\n  default: gpt-5.4-mini\n", encoding="utf-8")
(SANDBOX / ".env").write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")

spec = importlib.util.spec_from_file_location("envs", PLUGIN_DIR / "environments.py")
envs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(envs)
envs.default_home = lambda: SANDBOX

KEY = "stg-gateway-key-0123456789abcdef"
envs.save_settings("stg", url="https://api.tcc-stg.com/mcp", mcp_api_key="m", gateway_key=KEY)
ok, detail = envs.ensure_profile("stg-user-2520153", bearer=KEY)
assert ok, detail
profile_home = envs.profile_dir("stg-user-2520153")

from gateway.config import load_gateway_config
from gateway.run import (
    _own_policy_open_startup_violation,
    _platform_binds_port,
    _profile_runtime_scope,
)

PASS, FAIL = [], []


def check(label, condition, detail=""):
    (PASS if condition else FAIL).append(label)
    print(("  ok   " if condition else "  FAIL ") + label + ((" — " + str(detail)) if detail and not condition else ""))


with _profile_runtime_scope(profile_home):
    config = load_gateway_config()

enabled = [p.value for p, c in config.platforms.items() if c.enabled]
check("no platform is enabled", enabled == [], enabled)

port_binding = sorted(
    platform.value
    for platform, platform_config in config.platforms.items()
    if platform_config.enabled and _platform_binds_port(platform.value, platform_config.extra)
)
check("no port-binding platform (profile would be SKIPPED)", port_binding == [], port_binding)

violation = _own_policy_open_startup_violation(config)
check("no open-policy violation (would ABORT the gateway)", violation is None, violation)

# ── the property everything else rests on ────────────────────────────────
# Two profiles, two environments, one process: each turn must resolve ITS OWN
# MCP url/key. mcp_servers is not part of GatewayConfig — it is read by the
# agent through tools.mcp_tool._load_mcp_config(), whose ${VAR} interpolation
# goes through the profile secret scope when multiplexing is on. If that ever
# regressed to os.environ, every profile would silently talk to whichever
# environment the DEFAULT profile points at.
envs.save_settings("prod", url="https://api.theconcert.com/mcp", mcp_api_key="prod-mcp", gateway_key="prod-gateway-key-0123456789")
ok, detail = envs.ensure_profile("prod-user-99", bearer="prod-gateway-key-0123456789")
assert ok, detail

from agent.secret_scope import set_multiplex_active
from tools.mcp_tool import _load_mcp_config

set_multiplex_active(True)


def resolved(home, env):
    with _profile_runtime_scope(home):
        servers = _load_mcp_config() or {}
        return servers, (servers.get(envs.mcp_server_name(env)) or {})


stg_servers, stg = resolved(profile_home, "stg")
prod_servers, prod = resolved(envs.profile_dir("prod-user-99"), "prod")

# The connection pool in tools.mcp_tool is keyed by server NAME alone, so two
# environments sharing a name would silently share one connection to whichever
# connected first. Distinct names are the whole fix — assert it explicitly.
check("stg declares tcc-api-stg", list(stg_servers) == ["tcc-api-stg"], list(stg_servers))
check("prod declares tcc-api-prod", list(prod_servers) == ["tcc-api-prod"], list(prod_servers))
check("server names differ between environments", list(stg_servers) != list(prod_servers))

check("stg profile resolves the staging MCP url", stg.get("url") == "https://api.tcc-stg.com/mcp", stg.get("url"))
check("prod profile resolves the production MCP url", prod.get("url") == "https://api.theconcert.com/mcp", prod.get("url"))
check("the two profiles differ", stg.get("url") != prod.get("url"))
check(
    "stg carries its own bearer",
    (stg.get("headers") or {}).get("Authorization") == "Bearer m",
    (stg.get("headers") or {}).get("Authorization"),
)
check(
    "prod carries its own bearer",
    (prod.get("headers") or {}).get("Authorization") == "Bearer prod-mcp",
    (prod.get("headers") or {}).get("Authorization"),
)
check("no placeholder leaked through unresolved", "${" not in str(stg) + str(prod))

# The gateway resolves api_server as enabled from the inherited process env
# unless the profile disables it explicitly — the exact bug that silently
# skipped both base profiles earlier. Guard against a regression in the template.
text = (profile_home / "config.yaml").read_text(encoding="utf-8")
check("api_server disabled explicitly, not merely omitted", "enabled: false" in text)

shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
