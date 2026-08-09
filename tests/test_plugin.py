"""Exercise environments.py against a throwaway HERMES_HOME.

Run inside the hermes container. Touches nothing under the real /root/.hermes.
"""

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

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
    "MCP_TCC_MCP_API_KEY=stg-mcp-secret\n",
    encoding="utf-8",
)

spec = importlib.util.spec_from_file_location(
    "envs", Path(sys.argv[1]) / "environments.py"
)
envs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(envs)
envs.default_home = lambda: SANDBOX  # pin to the sandbox

PASS, FAIL = [], []


def check(label, condition, detail=""):
    (PASS if condition else FAIL).append(label)
    print(("  ok   " if condition else "  FAIL ") + label + ((" — " + str(detail)) if detail and not condition else ""))


print("\n1. profile name validation")
for good in ("stg-user-2520153", "prod-staff-689", "local-user-1",
             "stg-user-2520153-store-5002047", "local-user-7-store-9"):
    check(f"accepts {good}", bool(envs.PROFILE_RE.match(good)))
for bad in (
    "stg-user-",           # no id
    "stg-staff-1-store-2",     # staff never carries a store
    "stg-user-1-store-",       # store keyword with no id
    "stg-admin-1",         # unknown principal type
    "qa-user-1",           # unknown environment
    "../../etc/passwd",        # traversal
    "stg-user-1/../x",     # traversal via suffix
    "STG-user-1",          # uppercase (hermes _PROFILE_ID_RE rejects too)
    "stg-user-1234567890123",  # id too long
    "default",
):
    check(f"rejects {bad!r}", not envs.PROFILE_RE.match(bad))

print("\n2. env aliases")
check("staging -> stg", envs.normalize_env("staging") == "stg")
check("production -> prod", envs.normalize_env("Production") == "prod")
check("unknown -> None", envs.normalize_env("qa") is None)

print("\n3. settings round-trip")
envs.save_settings("stg", url="https://api.tcc-stg.com/mcp", mcp_api_key="stg-mcp", gateway_key="stg-gateway-key-0123456789")
settings = envs.get_settings("stg")
check("url stored", settings["url"] == "https://api.tcc-stg.com/mcp")
check("mcp key stored", settings["mcp_api_key"] == "stg-mcp")
check("gateway key stored", settings["gateway_key"] == "stg-gateway-key-0123456789")
check("prod still empty", envs.get_settings("prod")["url"] == "")
check(".env is 0600", oct((SANDBOX / ".env").stat().st_mode & 0o777) == "0o600")
check("unrelated keys preserved", "TELEGRAM_BOT_TOKEN" in envs.read_env_file(SANDBOX / ".env"))

print("\n4. gateway key check")
check("correct key accepted", envs.check_gateway_key("stg", "stg-gateway-key-0123456789"))
check("wrong key refused", not envs.check_gateway_key("stg", "nope"))
check("empty refused", not envs.check_gateway_key("stg", ""))
check("unconfigured env refused", not envs.check_gateway_key("prod", "stg-gateway-key-0123456789"))
envs.save_settings("local", gateway_key="short")
check("short key refused", not envs.check_gateway_key("local", "short"))

print("\n5. provisioning")
ok, detail = envs.ensure_profile("stg-user-2520153", bearer="stg-gateway-key-0123456789")
check("creates on first call", ok and detail == "created", detail)
ok, detail = envs.ensure_profile("stg-user-2520153", bearer="stg-gateway-key-0123456789")
check("idempotent", ok and detail == "exists", detail)
ok, detail = envs.ensure_profile("stg-user-999", bearer="wrong-key")
check("refuses wrong bearer", not ok and detail == "unauthorized", detail)
check("no dir left behind", not envs.profile_dir("stg-user-999").exists())
ok, detail = envs.ensure_profile("prod-user-1", bearer="stg-gateway-key-0123456789")
check("staging key cannot make prod", not ok, detail)
ok, detail = envs.ensure_profile("../escape", bearer="stg-gateway-key-0123456789")
check("refuses traversal", not ok and detail == "invalid profile name", detail)

print("\n6. materialized profile contents")
root = envs.profile_dir("stg-user-2520153")
config = (root / "config.yaml").read_text(encoding="utf-8")
env_values = envs.read_env_file(root / ".env")
check("api_server explicitly disabled", "api_server:\n    enabled: false" in config)
check("per-env mcp server name", "tcc-api-stg:" in config)
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
check("provider key copied", env_values.get("OPENAI_API_KEY") == "sk-provider-secret")
check("provider base_url copied", env_values.get("OPENAI_BASE_URL") == "https://api.example.com/v1")
check("platform token NOT copied", "TELEGRAM_BOT_TOKEN" not in env_values)
check("profile .env is 0600", oct((root / ".env").stat().st_mode & 0o777) == "0o600")
check("memories dir created", (root / "memories").is_dir())
check("no temp dirs left", not [p for p in envs.profiles_root().iterdir() if p.name.startswith(".tmp-")])

print("\n6b. a half-deleted profile repairs itself")
import shutil as _sh
broken = envs.profile_dir("stg-staff-4242")
envs.ensure_profile("stg-staff-4242", bearer="stg-gateway-key-0123456789")
# what the dashboard's delete + hermes' skeleton recreation leaves behind
(broken / ".env").unlink()
(broken / "config.yaml").unlink()
check("detected as incomplete", not envs.is_complete(broken))
ok, detail = envs.ensure_profile("stg-staff-4242", bearer="stg-gateway-key-0123456789")
check("repaired, not skipped as 'exists'", ok and detail == "repaired", detail)
check("API_SERVER_KEY restored", bool(envs.read_env_file(broken / ".env").get("API_SERVER_KEY")))
check("config.yaml restored", (broken / "config.yaml").is_file())
check("now complete", envs.is_complete(broken))
ok, detail = envs.ensure_profile("stg-staff-4242", bearer="stg-gateway-key-0123456789")
check("second call is a cheap no-op", ok and detail == "exists", detail)
_sh.rmtree(broken, ignore_errors=True)

print("\n7. memory isolation between users")
envs.ensure_profile("stg-staff-689", bearer="stg-gateway-key-0123456789")
a = envs.profile_dir("stg-user-2520153") / "memories" / "MEMORY.md"
b = envs.profile_dir("stg-staff-689") / "memories" / "MEMORY.md"
a.write_text("- user 2520153 likes weekly summaries\n", encoding="utf-8")
b.write_text("- staff 689 works on refunds\n", encoding="utf-8")
check("separate files", a.read_text() != b.read_text())
check("counts per env", envs.list_profiles()["stg"] == 2, envs.list_profiles())

print("\n8. resync after a key rotation")
envs.save_settings("stg", gateway_key="rotated-gateway-key-abcdefghij")
check("resync reports success", envs.resync_profile("stg-user-2520153"))
check(
    "profile picked up new key",
    envs.read_env_file(root / ".env").get("API_SERVER_KEY") == "rotated-gateway-key-abcdefghij",
)
check("memory survived resync", a.read_text().strip() == "- user 2520153 likes weekly summaries")

shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
