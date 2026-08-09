"""Verify the configured-vs-live distinction the dashboard reports.

The dashboard used to call an environment "ready" as soon as it was saved, but
Hermes only connects MCP servers at gateway startup — so a saved-but-not-yet-
restarted environment serves zero tools. These checks pin that distinction down.
"""

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

SANDBOX = Path(tempfile.mkdtemp(prefix="tcc-live-home-"))
(SANDBOX / "profiles").mkdir(parents=True)
(SANDBOX / "config.yaml").write_text("model:\n  default: gpt-5.4-mini\n", encoding="utf-8")
(SANDBOX / ".env").write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")

spec = importlib.util.spec_from_file_location("envs", Path(sys.argv[1]) / "environments.py")
envs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(envs)
envs.default_home = lambda: SANDBOX

PASS, FAIL = [], []


def check(label, condition, detail=""):
    (PASS if condition else FAIL).append(label)
    print(("  ok   " if condition else "  FAIL ") + label + ((" — " + str(detail)) if detail and not condition else ""))


print("\n1. nothing configured")
live = envs.live_environments()
check("no environment is live", not any(live.values()), live)

print("\n2. configured but gateway not restarted")
envs.save_settings("stg", url="https://api.tcc-stg.com/mcp", mcp_api_key="k1", gateway_key="stg-gateway-key-0123456789")
live = envs.live_environments()
check("stg is NOT live before a restart", not live["stg"])

print("\n3. gateway starts (mark_live)")
envs.mark_live()
live = envs.live_environments()
check("stg is live", live["stg"])
check("prod still not live", not live["prod"])

print("\n4. key rotated after startup")
envs.save_settings("stg", mcp_api_key="k2")
check("stg drops back to NOT live", not envs.live_environments()["stg"])
envs.mark_live()
check("live again after restart", envs.live_environments()["stg"])

print("\n5. url repointed after startup")
envs.save_settings("stg", url="https://api.theconcert.com/mcp")
check("url change also marks NOT live", not envs.live_environments()["stg"])

print("\n6. default profile gets every configured server declared")
envs.save_settings("stg", url="https://api.tcc-stg.com/mcp", mcp_api_key="k2")
envs.save_settings("local", url="http://host.docker.internal:3333/mcp", mcp_api_key="lk", gateway_key="local-gateway-key-0123456789")
envs.sync_default_profile_servers()
text = (SANDBOX / "config.yaml").read_text(encoding="utf-8")
check("tcc-api-stg declared", "tcc-api-stg:" in text)
check("tcc-api-local declared", "tcc-api-local:" in text)
check("tcc-api-prod NOT declared (unconfigured)", "tcc-api-prod:" not in text)
check("second call is a no-op", envs.sync_default_profile_servers() is False)

print("\n7. an environment that gets un-configured is removed, not left broken")
envs.save_settings("local", url="")
removed = envs.sync_default_profile_servers()
text = (SANDBOX / "config.yaml").read_text(encoding="utf-8")
check("sync reports a change", removed)
check("tcc-api-local removed", "tcc-api-local:" not in text)
check("tcc-api-stg still there", "tcc-api-stg:" in text)

print("\n8. the live marker never stores a key")
marker = (SANDBOX / ".tcc-mcp-config-live.json").read_text(encoding="utf-8")
check("no MCP key in the marker file", "k2" not in marker and "lk" not in marker, marker[:120])

shutil.rmtree(SANDBOX, ignore_errors=True)
print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
