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



def main():
    PLUGIN_DIR = Path(sys.argv[1])
    SANDBOX = Path(tempfile.mkdtemp(prefix="tcc-startup-home-"))
    (SANDBOX / "profiles").mkdir(parents=True)
    (SANDBOX / "config.yaml").write_text("model:\n  default: gpt-5.4-mini\n", encoding="utf-8")
    (SANDBOX / ".env").write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")

    spec = importlib.util.spec_from_file_location("envs", PLUGIN_DIR / "environments.py")
    envs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(envs)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _sandbox
    _sandbox.pin(envs, SANDBOX)

    KEY = "stg-gateway-key-0123456789abcdef"
    envs.save_settings(url="https://api.tcc-stg.com/mcp", mcp_api_key="m", gateway_key=KEY)
    ok, detail = envs.ensure_profile("user-2520153", bearer=KEY)
    assert ok, detail
    profile_home = envs.profile_dir("user-2520153")

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

    # Two users, one MCP: they share the server name and URL (one Droplet Hermes)
    # but keep isolated memory. mcp_servers is not part of GatewayConfig — it is
    # read by the agent through tools.mcp_tool._load_mcp_config().
    ok, detail = envs.ensure_profile("staff-689", bearer=KEY)
    assert ok, detail

    from agent.secret_scope import set_multiplex_active
    from tools.mcp_tool import _load_mcp_config

    set_multiplex_active(True)


    def resolved(home):
        with _profile_runtime_scope(home):
            servers = _load_mcp_config() or {}
            return servers, (servers.get(envs.mcp_server_name()) or {})


    user_servers, user_mcp = resolved(profile_home)
    staff_servers, staff_mcp = resolved(envs.profile_dir("staff-689"))

    check("user declares tcc-api", list(user_servers) == ["tcc-api"], list(user_servers))
    check("staff declares tcc-api", list(staff_servers) == ["tcc-api"], list(staff_servers))
    check("both users share the server name", list(user_servers) == list(staff_servers))
    check("user resolves the MCP url", user_mcp.get("url") == "https://api.tcc-stg.com/mcp", user_mcp.get("url"))
    check("staff resolves the same MCP url", staff_mcp.get("url") == "https://api.tcc-stg.com/mcp", staff_mcp.get("url"))
    check(
        "user carries the bearer",
        (user_mcp.get("headers") or {}).get("Authorization") == "Bearer m",
        (user_mcp.get("headers") or {}).get("Authorization"),
    )
    check(
        "staff carries the same bearer",
        (staff_mcp.get("headers") or {}).get("Authorization") == "Bearer m",
        (staff_mcp.get("headers") or {}).get("Authorization"),
    )
    check("no placeholder leaked through unresolved", "${" not in str(user_mcp) + str(staff_mcp))

    user_mem = profile_home / "memories"
    staff_mem = envs.profile_dir("staff-689") / "memories"
    (user_mem / "MEMORY.md").write_text("- user 2520153 likes weekly summaries\n", encoding="utf-8")
    (staff_mem / "MEMORY.md").write_text("- staff 689 works on refunds\n", encoding="utf-8")
    check(
        "memory files are isolated",
        (user_mem / "MEMORY.md").read_text() != (staff_mem / "MEMORY.md").read_text(),
    )

    # The gateway resolves api_server as enabled from the inherited process env
    # unless the profile disables it explicitly — the exact bug that silently
    # skipped both base profiles earlier. Guard against a regression in the template.
    text = (profile_home / "config.yaml").read_text(encoding="utf-8")
    check("api_server disabled explicitly, not merely omitted", "enabled: false" in text)

    shutil.rmtree(SANDBOX, ignore_errors=True)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
