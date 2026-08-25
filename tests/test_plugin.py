"""Exercise environments.py against a throwaway HERMES_HOME.

Run inside the hermes container. Touches nothing under the real /root/.hermes.
"""

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path



def main():
    SANDBOX = Path(tempfile.mkdtemp(prefix="tcc-test-home-"))
    (SANDBOX / "profiles").mkdir(parents=True)
    (SANDBOX / "config.yaml").write_text(
        "model:\n  default: gpt-5.4-mini\n  provider: openai-api\n  base_url: ''\n"
        "agent:\n  max_turns: 150\n",
        encoding="utf-8",
    )
    (SANDBOX / ".env").write_text(
        "API_SERVER_KEY=legacy-default-key-0123456789abcdef\n"
        "OPENAI_API_KEY=sk-provider-secret\n"
        "OPENAI_BASE_URL=https://api.example.com/v1\n"
        "TELEGRAM_BOT_TOKEN=must-not-be-copied\n"
        "TCC_STAGING_MCP_URL=https://api.tcc-stg.com/mcp\n"
        "MCP_TCC_MCP_API_KEY=stg-mcp-secret\n"
        "TCC_MCP_URL_LOCAL=http://host.docker.internal:3333/mcp\n"
        "TCC_MCP_KEY_LOCAL=local-mcp-secret\n"
        "TCC_GATEWAY_KEY_LOCAL=local-gateway-key-0123456789\n",
        encoding="utf-8",
    )

    spec = importlib.util.spec_from_file_location(
        "envs", Path(sys.argv[1]) / "environments.py"
    )
    envs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(envs)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _sandbox
    _sandbox.pin(envs, SANDBOX)

    PASS, FAIL = [], []


    def check(label, condition, detail=""):
        (PASS if condition else FAIL).append(label)
        print(("  ok   " if condition else "  FAIL ") + label + ((" — " + str(detail)) if detail and not condition else ""))


    print("\n1. profile name validation")
    for good in ("staff-689", "user-2520153", "user-1",
                 "user-2520153-store-5002047", "user-7-store-9",
                 "organizer-5", "organizer-5-store-9"):
        check(f"accepts {good}", bool(envs.PROFILE_RE.match(good)))
    for bad in (
        "user-",                      # no id
        "staff-1-store-2",            # staff never carries a store
        "user-1-store-",              # store keyword with no id
        "admin-1",                    # unknown principal type
        "stg-user-1",                 # leftover env prefix is not a new name
        "local-staff-3",
        "../../etc/passwd",           # traversal
        "user-1/../x",                # traversal via suffix
        "STAFF-1",                    # uppercase (hermes _PROFILE_ID_RE rejects too)
        "user-1234567890123",         # id too long
        "default",
    ):
        check(f"rejects {bad!r}", not envs.PROFILE_RE.match(bad))

    print("\n2. migrate legacy LOCAL/STG keys on read")
    settings = envs.get_settings()
    check("url from first filled (LOCAL before STG)", settings["url"] == "http://host.docker.internal:3333/mcp")
    check("mcp key from LOCAL", settings["mcp_api_key"] == "local-mcp-secret")
    check("gateway key from LOCAL", settings["gateway_key"] == "local-gateway-key-0123456789")
    raw = envs.read_env_file(SANDBOX / ".env")
    check("new unsuffixed keys written", raw.get("TCC_MCP_URL") == "http://host.docker.internal:3333/mcp")
    check("old LOCAL key left in place", raw.get("TCC_MCP_URL_LOCAL") == "http://host.docker.internal:3333/mcp")
    check("old STG key left in place", raw.get("TCC_STAGING_MCP_URL") == "https://api.tcc-stg.com/mcp")

    print("\n3. settings round-trip")
    envs.save_settings(url="https://api.tcc-stg.com/mcp", mcp_api_key="stg-mcp", gateway_key="stg-gateway-key-0123456789")
    settings = envs.get_settings()
    check("url stored", settings["url"] == "https://api.tcc-stg.com/mcp")
    check("mcp key stored", settings["mcp_api_key"] == "stg-mcp")
    check("gateway key stored", settings["gateway_key"] == "stg-gateway-key-0123456789")
    check(".env is 0600", oct((SANDBOX / ".env").stat().st_mode & 0o777) == "0o600")
    check("unrelated keys preserved", "TELEGRAM_BOT_TOKEN" in envs.read_env_file(SANDBOX / ".env"))
    # Saving a new key must not clobber leftover suffixed keys.
    check("legacy LOCAL leftover still present", "TCC_MCP_URL_LOCAL" in envs.read_env_file(SANDBOX / ".env"))

    print("\n4. gateway key check")
    check("correct key accepted", envs.check_gateway_key("stg-gateway-key-0123456789"))
    check("wrong key refused", not envs.check_gateway_key("nope"))
    check("empty refused", not envs.check_gateway_key(""))
    # Short keys never authenticate, even if someone wrote one by hand.
    envs.save_settings(gateway_key="short")
    check("short key refused", not envs.check_gateway_key("short"))
    envs.save_settings(gateway_key="stg-gateway-key-0123456789")

    print("\n5. provisioning")
    ok, detail = envs.ensure_profile("user-2520153", bearer="stg-gateway-key-0123456789")
    check("creates on first call", ok and detail == "created", detail)
    ok, detail = envs.ensure_profile("user-2520153", bearer="stg-gateway-key-0123456789")
    check("idempotent", ok and detail == "exists", detail)
    ok, detail = envs.ensure_profile("user-999", bearer="wrong-key")
    check("refuses wrong bearer", not ok and detail == "unauthorized", detail)
    check("no dir left behind", not envs.profile_dir("user-999").exists())
    ok, detail = envs.ensure_profile("../escape", bearer="stg-gateway-key-0123456789")
    check("refuses traversal", not ok and detail == "invalid profile name", detail)
    ok, detail = envs.ensure_profile("stg-user-1", bearer="stg-gateway-key-0123456789")
    check("refuses leftover env-prefixed name", not ok and detail == "invalid profile name", detail)

    print("\n6. materialized profile contents")
    root = envs.profile_dir("user-2520153")
    config = (root / "config.yaml").read_text(encoding="utf-8")
    env_values = envs.read_env_file(root / ".env")
    check("api_server explicitly disabled", "api_server:\n    enabled: false" in config)
    check("mcp server name is tcc-api", "tcc-api:" in config)
    check("mcp url written as a literal", "url: 'https://api.tcc-stg.com/mcp'" in config)
    check("mcp bearer written as a literal", "Bearer stg-mcp'" in config)
    check("no ${} placeholder left to leak the default profile", "${" not in config)
    check("config.yaml is 0600 (holds the bearer)", oct((root / "config.yaml").stat().st_mode & 0o777) == "0o600")
    check("memory toolset enabled", "- memory" in config)
    check("memory enabled", "memory_enabled: true" in config)
    check("model block carried over", "gpt-5.4-mini" in config)
    check("API_SERVER_KEY = env gateway key", env_values.get("API_SERVER_KEY") == "stg-gateway-key-0123456789")
    check("MCP url materialized", env_values.get("TCC_ACTIVE_MCP_URL") == "https://api.tcc-stg.com/mcp")
    check("MCP key materialized", env_values.get("TCC_ACTIVE_MCP_API_KEY") == "stg-mcp")
    check("no TCC_ACTIVE_MCP_ENV", "TCC_ACTIVE_MCP_ENV" not in env_values)
    check("provider key copied", env_values.get("OPENAI_API_KEY") == "sk-provider-secret")
    check("provider base_url copied", env_values.get("OPENAI_BASE_URL") == "https://api.example.com/v1")
    check("platform token NOT copied", "TELEGRAM_BOT_TOKEN" not in env_values)
    check("profile .env is 0600", oct((root / ".env").stat().st_mode & 0o777) == "0o600")
    check("memories dir created", (root / "memories").is_dir())
    check("no temp dirs left", not [p for p in envs.profiles_root().iterdir() if p.name.startswith(".tmp-")])

    print("\n6b. a half-deleted profile repairs itself")
    import shutil as _sh
    broken = envs.profile_dir("staff-4242")
    envs.ensure_profile("staff-4242", bearer="stg-gateway-key-0123456789")
    # what the dashboard's delete + hermes' skeleton recreation leaves behind
    (broken / ".env").unlink()
    (broken / "config.yaml").unlink()
    check("detected as incomplete", not envs.is_complete(broken))
    ok, detail = envs.ensure_profile("staff-4242", bearer="stg-gateway-key-0123456789")
    check("repaired, not skipped as 'exists'", ok and detail == "repaired", detail)
    check("API_SERVER_KEY restored", bool(envs.read_env_file(broken / ".env").get("API_SERVER_KEY")))
    check("config.yaml restored", (broken / "config.yaml").is_file())
    check("now complete", envs.is_complete(broken))
    ok, detail = envs.ensure_profile("staff-4242", bearer="stg-gateway-key-0123456789")
    check("second call is a cheap no-op", ok and detail == "exists", detail)
    _sh.rmtree(broken, ignore_errors=True)

    print("\n7. memory isolation between users")
    envs.ensure_profile("staff-689", bearer="stg-gateway-key-0123456789")
    a = envs.profile_dir("user-2520153") / "memories" / "MEMORY.md"
    b = envs.profile_dir("staff-689") / "memories" / "MEMORY.md"
    a.write_text("- user 2520153 likes weekly summaries\n", encoding="utf-8")
    b.write_text("- staff 689 works on refunds\n", encoding="utf-8")
    check("separate files", a.read_text() != b.read_text())
    check("profile count is an int", envs.list_profiles() == 2, envs.list_profiles())

    print("\n8. resync after a key rotation")
    envs.save_settings(gateway_key="rotated-gateway-key-abcdefghij")
    check("resync reports success", envs.resync_profile("user-2520153"))
    check(
        "profile picked up new key",
        envs.read_env_file(root / ".env").get("API_SERVER_KEY") == "rotated-gateway-key-abcdefghij",
    )
    check("memory survived resync", a.read_text().strip() == "- user 2520153 likes weekly summaries")

    print("\n9. do not overwrite new keys on migrate")
    envs.save_settings(url="https://api.theconcert.com/mcp")
    # Re-introduce a competing LOCAL leftover; migrate must leave the new URL alone.
    raw = envs.read_env_file(SANDBOX / ".env")
    raw["TCC_MCP_URL_LOCAL"] = "http://should-not-win/mcp"
    envs.write_env_file(SANDBOX / ".env", raw)
    check("migrate is a no-op once new keys exist", envs.migrate_legacy_settings() is False)
    check("new url untouched", envs.get_settings()["url"] == "https://api.theconcert.com/mcp")

    shutil.rmtree(SANDBOX, ignore_errors=True)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
