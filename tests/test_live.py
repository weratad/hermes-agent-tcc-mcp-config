"""Verify the configured-vs-live distinction the dashboard reports.

The dashboard used to call MCP "ready" as soon as it was saved, but Hermes
only connects MCP servers at gateway startup — so a saved-but-not-yet-
restarted config serves zero tools. These checks pin that distinction down.
"""

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path



def main():
    SANDBOX = Path(tempfile.mkdtemp(prefix="tcc-live-home-"))
    (SANDBOX / "profiles").mkdir(parents=True)
    (SANDBOX / "config.yaml").write_text(
        "model:\n  default: gpt-5.4-mini\n"
        "mcp_servers:\n  tcc-api-stg:\n    url: https://old.example/mcp\n",
        encoding="utf-8",
    )
    (SANDBOX / ".env").write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")

    spec = importlib.util.spec_from_file_location("envs", Path(sys.argv[1]) / "environments.py")
    envs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(envs)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _sandbox
    _sandbox.pin(envs, SANDBOX)

    PASS, FAIL = [], []


    def check(label, condition, detail=""):
        (PASS if condition else FAIL).append(label)
        print(("  ok   " if condition else "  FAIL ") + label + ((" — " + str(detail)) if detail and not condition else ""))


    print("\n1. nothing configured")
    check("not live", envs.is_live() is False)

    print("\n2. configured but gateway not restarted")
    envs.save_settings(url="https://api.tcc-stg.com/mcp", mcp_api_key="k1", gateway_key="stg-gateway-key-0123456789")
    check("NOT live before a restart", envs.is_live() is False)

    print("\n3. gateway starts (mark_live)")
    envs.mark_live()
    check("live after mark_live", envs.is_live() is True)

    print("\n4. key rotated after startup")
    envs.save_settings(mcp_api_key="k2")
    check("drops back to NOT live", envs.is_live() is False)
    envs.mark_live()
    check("live again after restart", envs.is_live() is True)

    print("\n5. url repointed after startup")
    envs.save_settings(url="https://api.theconcert.com/mcp")
    check("url change also marks NOT live", envs.is_live() is False)

    print("\n6. default profile gets the one server declared")
    envs.save_settings(url="https://api.tcc-stg.com/mcp", mcp_api_key="k2")
    envs.sync_default_profile_servers()
    text = (SANDBOX / "config.yaml").read_text(encoding="utf-8")
    check("tcc-api declared", "tcc-api:" in text)
    check("leftover tcc-api-stg removed", "tcc-api-stg:" not in text)
    check("leftover tcc-catalog removed", "tcc-catalog:" not in text)
    check("second call is a no-op", envs.sync_default_profile_servers() is False)

    print("\n7. un-configuring removes the server")
    envs.save_settings(url="")
    removed = envs.sync_default_profile_servers()
    text = (SANDBOX / "config.yaml").read_text(encoding="utf-8")
    check("sync reports a change", removed)
    check("tcc-api removed", "tcc-api:" not in text)

    print("\n8. the live marker never stores a key")
    marker = (SANDBOX / ".tcc-mcp-config-live.json").read_text(encoding="utf-8")
    check("no MCP key in the marker file", "k2" not in marker, marker[:120])

    shutil.rmtree(SANDBOX, ignore_errors=True)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED: " + ", ".join(FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
