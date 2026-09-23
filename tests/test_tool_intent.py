"""ToolIntent classifier unit tests (no live OpenAI).

Run: python3 tests/test_tool_intent.py [/path/to/tcc-mcp-config]
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path


def main():
    PLUGIN_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent

    pkg_name = "tcc_mcp_config"
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(PLUGIN_DIR)]
    sys.modules[pkg_name] = pkg

    ti_spec = importlib.util.spec_from_file_location(f"{pkg_name}.tool_intent", PLUGIN_DIR / "tool_intent.py")
    ti = importlib.util.module_from_spec(ti_spec)
    sys.modules[f"{pkg_name}.tool_intent"] = ti
    assert ti_spec.loader is not None
    ti_spec.loader.exec_module(ti)

    PASS, FAIL = [], []
    orig_call = ti._call_openai_intent

    def check(label, condition, detail=""):
        (PASS if condition else FAIL).append(label)
        print(("  ok   " if condition else "  FAIL ") + label + ((" — " + str(detail)) if detail and not condition else ""))

    def restore_call():
        ti._call_openai_intent = orig_call

    print("\n1. context-only share chip")
    check("blank text → other", ti.classify_tool_intent_sync("   ") == "other")
    check("แชร์ให้เพื่อนดู → other", ti.classify_tool_intent_sync("แชร์ให้เพื่อนดู") == "other")

    print("\n2. price-compare asks → event (before OpenAI)")
    ti._call_openai_intent = lambda *a, **k: (_ for _ in ()).throw(AssertionError("OpenAI must not run"))
    check(
        "เทียบราคาบัตร Sakon Festival → event",
        ti.classify_tool_intent_sync("เทียบราคาบัตร Sakon Festival") == "event",
    )
    check(
        "Compare Sakon Festival 2026 ticket prices → event",
        ti.classify_tool_intent_sync("Compare Sakon Festival 2026 ticket prices") == "event",
    )
    check(
        "โซนไหนคุ้มสุด → event",
        ti.classify_tool_intent_sync("โซนไหนคุ้มสุด") == "event",
    )
    check(
        "แชร์ให้เพื่อนดู stays other (context-only)",
        ti.classify_tool_intent_sync("แชร์ให้เพื่อนดู") == "other",
    )
    ti._call_openai_intent = lambda *a, **k: "other"
    check(
        "nightlife ask can still be other",
        ti.classify_tool_intent_sync("หาร้านจองโต๊ะใกล้สีลม") == "other",
    )
    restore_call()

    print("\n3. fail-closed on OpenAI failure (no event heuristic)")
    ti._call_openai_intent = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no api"))
    check(
        "concert query when API fails → other",
        ti.classify_tool_intent_sync("คอนเสิร์ตใกล้ฉัน") == "other",
    )
    restore_call()

    print("\n4. OpenAI parsed labels")
    ti._call_openai_intent = lambda *a, **k: "event"
    check("event label", ti.classify_tool_intent_sync("มีคอนอะไรน่าไปบ้าง") == "event")
    ti._call_openai_intent = lambda *a, **k: "similar"
    check("similar label", ti.classify_tool_intent_sync("คอนคล้ายๆ STARRY") == "similar")
    restore_call()

    print("\n5. invalid label → other")
    ti._call_openai_intent = lambda *a, **k: "weather"
    check("weather → other", ti.classify_tool_intent_sync("อากาศวันนี้") == "other")
    restore_call()

    print("\n6. _resolve_openai_api_key from Hermes config")
    orig_env = os.environ.get("OPENAI_API_KEY")
    orig_paths = ti._hermes_config_paths
    tmp_dir = Path(os.environ.get("TMPDIR", "/tmp")) / "tcc_tool_intent_key_test"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    cfg = tmp_dir / "config.yaml"
    cfg.write_text(
        "providers:\n  oai:\n    api_key: sk-test-from-yaml-only\n",
        encoding="utf-8",
    )
    try:
        os.environ.pop("OPENAI_API_KEY", None)
        ti._hermes_config_paths = lambda: [cfg]
        resolved = ti._resolve_openai_api_key()
        check(
            "config providers.oai.api_key when env empty",
            resolved == "sk-test-from-yaml-only",
            f"got len={len(resolved)}",
        )
        missing = tmp_dir / "missing.yaml"
        ti._hermes_config_paths = lambda: [missing]
        check(
            "empty when no config key",
            ti._resolve_openai_api_key() == "",
        )
        check(
            "classify fail-closes without key",
            ti.classify_tool_intent_sync("คอนเสิร์ตใกล้ฉัน") == "other",
        )
    finally:
        ti._hermes_config_paths = orig_paths
        if orig_env is not None:
            os.environ["OPENAI_API_KEY"] = orig_env
        else:
            os.environ.pop("OPENAI_API_KEY", None)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED: " + ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
