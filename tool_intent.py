"""Catalog ToolIntent classifier (mirrors AIS ai_ask/tool_intent.ts)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
import importlib.util as _ilu
from typing import Any

ALLOWED = frozenset({"event", "similar", "other", "refuse"})
CONTEXT_ONLY_RE = re.compile(r"^แชร์ให้เพื่อนดู(?:\s|$)")

TOOL_INTENT_PROMPT = "\n".join(
    [
        "จำแนก intent ของข้อความล่าสุดโดยใช้ประวัติสนทนาประกอบ",
        "similar: ผู้ใช้ต้องการคอนเสิร์ตที่คล้ายกับงานที่ระบุชื่อ หรือคล้ายกับงานที่กำลังคุยอยู่ ทั้งแนว ไลน์อัพ หรืออารมณ์ของงานนั้น",
        "event: การค้นหา แนะนำ เปรียบเทียบ หรือถามรายละเอียดคอนเสิร์ต เทศกาล อีเวนต์ดนตรี ศิลปิน ไลน์อัพ บัตร ราคา วันเวลา และสถานที่จัดงาน รวมถึงคำถามต่อเนื่องจากรายการงาน ที่ไม่ใช่การหากลุ่มงานที่คล้ายกัน",
        "other: ร้าน ผับ บาร์ คาเฟ่ สถานที่ดนตรีสด จองโต๊ะ คืนเงิน และคำถามเกี่ยวกับร้าน/ไนท์ไลฟ์ในระบบ",
        "refuse: นอกขอบเขต — สอนทำเกม โค้ด การบ้าน อากาศ สูตรอาหาร สุขภาพ การเงินทั่วไป ความรู้ทั่วไป และแชทที่ไม่เกี่ยวคอนเสิร์ต เทศกาล อีเวนต์ดนตรี ศิลปิน ร้าน หรือไนท์ไลฟ์",
    ]
)

_DEFAULT_MODEL = "gpt-5.4-mini"
_DEFAULT_TIMEOUT_S = 8.0

_log = logging.getLogger("hermes.plugin.tcc-mcp-config.tool-intent")


def _load_price_compare_format():
    try:
        from . import price_compare_format as mod  # type: ignore
        return mod
    except ImportError:
        pass
    path = Path(__file__).resolve().parent / "price_compare_format.py"
    spec = _ilu.spec_from_file_location("_tcc_tool_intent_price_compare_format", path)
    mod = _ilu.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


_price_compare_format = _load_price_compare_format()

_INTENT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["event", "similar", "other", "refuse"],
        }
    },
    "required": ["intent"],
    "additionalProperties": False,
}


def is_context_only_follow_up(text: str) -> bool:
    return bool(CONTEXT_ONLY_RE.match(str(text or "").strip()))


def classify_tool_intent_sync(text: str, history: list[dict] | None = None) -> str:
    if not str(text or "").strip():
        return "other"
    if is_context_only_follow_up(text):
        return "other"
    if _price_compare_format.is_price_compare_ask(text):
        return "event"
    try:
        label = _call_openai_intent(text, history or [])
    except Exception as exc:
        _log.warning("tool intent classification failed: %s", exc)
        return "other"
    return label if label in ALLOWED else "other"


async def classify_tool_intent(text: str, history: list[dict] | None = None) -> str:
    return await asyncio.to_thread(classify_tool_intent_sync, text, history)


def _openai_base_url() -> str:
    base = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    return base


def _hermes_config_paths() -> list[Path]:
    paths: list[Path] = []
    hermes_home = (os.environ.get("HERMES_HOME") or "").strip()
    if hermes_home:
        paths.append(Path(hermes_home) / "config.yaml")
    paths.append(Path("/opt/data/config.yaml"))
    paths.append(Path.home() / ".hermes" / "config.yaml")
    return paths


def _extract_oai_api_key_from_text(text: str) -> str | None:
    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(text)
        if isinstance(data, dict):
            providers = data.get("providers")
            if isinstance(providers, dict):
                oai = providers.get("oai")
                if isinstance(oai, dict):
                    key = oai.get("api_key")
                    if key is not None:
                        stripped = str(key).strip()
                        if stripped:
                            return stripped
    except Exception:
        pass

    lines = text.splitlines()
    in_providers = False
    providers_indent = -1
    in_oai = False
    oai_indent = -1

    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()

        if in_oai and indent <= oai_indent and line.endswith(":") and not line.startswith("api_key"):
            in_oai = False
        if in_providers and not in_oai and indent <= providers_indent and line.endswith(":"):
            if not line.startswith("providers"):
                in_providers = False

        match = re.match(r"^(providers|oai|api_key)\s*:\s*(.*)$", line)
        if not match:
            continue
        key_name, rest = match.group(1), match.group(2).strip()

        if key_name == "providers":
            in_providers = True
            providers_indent = indent
            in_oai = False
        elif key_name == "oai" and in_providers and indent > providers_indent:
            in_oai = True
            oai_indent = indent
        elif key_name == "api_key" and in_oai and indent > oai_indent:
            val = rest.strip().strip('"').strip("'")
            if val:
                return val
    return None


def _read_oai_api_key_from_config(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return _extract_oai_api_key_from_text(text)


def _resolve_openai_api_key() -> str:
    env_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if env_key:
        return env_key
    for path in _hermes_config_paths():
        key = _read_oai_api_key_from_config(path)
        if key:
            return key
    return ""


def _dedupe_latest_user_from_history(text: str, history: list[dict]) -> list[dict]:
    """Drop trailing history user turn when it duplicates the text being classified."""
    trimmed = str(text or "").strip()
    if not trimmed or not history:
        return history
    out = list(history)
    for index in range(len(out) - 1, -1, -1):
        message = out[index]
        if not isinstance(message, dict):
            continue
        if message.get("role") != "user":
            continue
        if str(message.get("content") or "").strip() == trimmed:
            return out[:index] + out[index + 1 :]
        break
    return out


def _build_input_messages(text: str, history: list[dict]) -> list[dict[str, str]]:
    history = _dedupe_latest_user_from_history(text, history)
    messages: list[dict[str, str]] = [{"role": "system", "content": TOOL_INTENT_PROMPT}]
    for message in history:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        content = message.get("content")
        if content is None:
            continue
        messages.append({"role": role, "content": str(content)})
    trimmed = messages[1:]
    if len(trimmed) > 12:
        trimmed = trimmed[-12:]
    messages = [messages[0], *trimmed]
    messages.append({"role": "user", "content": text})
    return messages


def _parse_intent_from_response(body: dict[str, Any]) -> str | None:
    parsed = body.get("output_parsed")
    if isinstance(parsed, dict) and isinstance(parsed.get("intent"), str):
        return parsed["intent"]

    for item in body.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in ("output_text", "text"):
                    raw = part.get("text") or part.get("content") or ""
                    if raw:
                        try:
                            data = json.loads(raw)
                            if isinstance(data, dict) and isinstance(data.get("intent"), str):
                                return data["intent"]
                        except json.JSONDecodeError:
                            pass
    return None


def _call_openai_intent(text: str, history: list[dict]) -> str:
    api_key = _resolve_openai_api_key()
    if not api_key:
        raise RuntimeError("OpenAI API key not configured")

    model = os.environ.get("TOOL_INTENT_MODEL") or _DEFAULT_MODEL
    timeout = float(os.environ.get("TOOL_INTENT_TIMEOUT_S") or _DEFAULT_TIMEOUT_S)

    payload = {
        "model": model,
        "reasoning": {"effort": "low"},
        "max_output_tokens": 256,
        "input": _build_input_messages(text, history),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "tool_intent",
                "schema": _INTENT_JSON_SCHEMA,
                "strict": True,
            }
        },
    }

    url = f"{_openai_base_url()}/responses"
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail}") from exc

    label = _parse_intent_from_response(body)
    if not label:
        raise RuntimeError("OpenAI response missing intent")
    return label
