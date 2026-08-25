"""Backend for the TCC MCP Config dashboard tab.

One MCP URL, one MCP API key, one gateway key. Per-user Hermes profiles
(``staff-<id>`` / ``user-<id>[-store-<id>]``) isolate memory; they all talk to
the same tcc-api ``/mcp``.

Storage and profile materialization both live in ``environments.py``, one
directory up, so this API and the in-gateway provisioner can never disagree
about a key name or a template. It is loaded by path rather than by relative
import because the dashboard mounts this file directly, without the plugin's
package context.

Secrets are never returned to the browser — only whether a key is set and its
last four characters.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

router = APIRouter()
_log = logging.getLogger("hermes.plugin.tcc-mcp-config")
_lock = asyncio.Lock()

_PROFILE_PARTS = re.compile(
    r"^(staff|user|organizer)-([0-9]{1,12})(?:-store-([0-9]{1,12}))?$"
)


def _load_environments():
    path = Path(__file__).resolve().parent.parent / "environments.py"
    spec = importlib.util.spec_from_file_location("tcc_mcp_config_environments", path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError("cannot load environments.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


envs = _load_environments()

MCP_SERVER_NAME = envs.MCP_SERVER_NAME


class SaveSettings(BaseModel):
    url: Optional[str] = None
    mcp_api_key: Optional[str] = None
    gateway_key: Optional[str] = None


def _check_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "url is required")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "url must start with http:// or https://")
    if any(char in url for char in "\r\n"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "url contains invalid characters")
    return url


def _mask(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    return f"…{value[-4:]}" if len(value) > 4 else "…"


def _describe(settings: dict, *, live: bool, profiles: int) -> dict:
    url = settings.get("url") or ""
    mcp_key = settings.get("mcp_api_key") or ""
    gateway_key = settings.get("gateway_key") or ""
    configured = bool(url and mcp_key and len(gateway_key) >= 16)
    return {
        "url": url,
        "mcp_key_set": bool(mcp_key),
        "mcp_key_hint": _mask(mcp_key),
        "gateway_key_set": bool(gateway_key),
        "gateway_key_hint": _mask(gateway_key),
        "gateway_key_weak": bool(gateway_key) and len(gateway_key) < 16,
        "configured": configured,
        # Hermes connects MCP servers once per gateway process, so "saved" and
        # "serving traffic" are different states. Reporting only the first would
        # show the slot as ready while chats have no tools at all.
        "live": configured and live,
        "mcp_server_name": MCP_SERVER_NAME,
        "profiles": profiles,
        "profile_example": "staff-123 / user-123-store-456",
        "restart_required": configured and not live,
    }


@router.get("/profiles")
async def list_profiles_detail(
    q: str = "",
    page: int = 1,
    page_size: int = 10,
    env: str = "",  # accepted and ignored — old dashboards sent ?env=
) -> dict:
    """List provisioned per-user profiles — search + paginate.

    Powers the dashboard's Profiles panel. Read-only: a directory scan of
    ``profiles/`` matched against ``PROFILE_RE``.
    """
    del env
    needle = (q or "").strip().lower()
    rows = []
    try:
        for entry in envs.profiles_root().iterdir():
            if not entry.is_dir():
                continue
            match = envs.PROFILE_RE.match(entry.name)
            if not match:
                continue
            if needle and needle not in entry.name.lower():
                continue
            parts = _PROFILE_PARTS.match(entry.name)
            ptype = parts.group(1) if parts else ""
            pid = parts.group(2) if parts else ""
            store = parts.group(3) if parts and parts.group(3) else ""
            try:
                mtime = int(entry.stat().st_mtime)
            except OSError:
                mtime = 0
            rows.append(
                {
                    "name": entry.name,
                    "type": ptype,
                    "id": pid,
                    "store": store,
                    "updated_at": mtime,
                }
            )
    except OSError:
        pass

    rows.sort(key=lambda r: (-r["updated_at"], r["name"]))
    total = len(rows)
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(page_size)
    except (TypeError, ValueError):
        page_size = 10
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    pages = (total + page_size - 1) // page_size if total else 1
    if page > pages:
        page = pages
    start = (page - 1) * page_size
    return {
        "items": rows[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "counts": envs.list_profiles(),
    }


@router.get("/settings")
async def get_settings() -> dict:
    async with _lock:
        migrated = envs.migrate_legacy_settings()
        settings = envs.get_settings()
    described = _describe(
        settings, live=envs.is_live(), profiles=envs.list_profiles()
    )
    return {
        "mcp_server_name": MCP_SERVER_NAME,
        "migrated": migrated,
        "restart_command": "docker restart tcc-hermes",
        **described,
    }


@router.put("/settings")
async def save_settings(body: SaveSettings) -> dict:
    async with _lock:
        url = _check_url(body.url) if body.url is not None else None
        mcp_api_key = (body.mcp_api_key or "").strip() or None
        gateway_key = (body.gateway_key or "").strip() or None
        if gateway_key is not None and len(gateway_key) < 16:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "gateway key ต้องยาวอย่างน้อย 16 ตัวอักษร",
            )
        if url is None and mcp_api_key is None and gateway_key is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "ไม่มีอะไรให้บันทึก")

        envs.save_settings(url=url, mcp_api_key=mcp_api_key, gateway_key=gateway_key)
        settings = envs.get_settings()
        resynced = _resync()
        # Declare the MCP server on the DEFAULT profile — that is the only
        # config Hermes reads when it connects MCP servers at startup.
        envs.sync_default_profile_servers()

    described = _describe(
        settings, live=envs.is_live(), profiles=envs.list_profiles()
    )
    return {
        "saved": True,
        "resynced_profiles": resynced,
        **described,
    }


def _resync() -> int:
    changed = 0
    try:
        for entry in envs.profiles_root().iterdir():
            if not entry.is_dir():
                continue
            if envs.PROFILE_RE.match(entry.name) and envs.resync_profile(entry.name):
                changed += 1
    except OSError:
        _log.warning("tcc-mcp-config: could not resync profiles", exc_info=True)
    return changed


@router.post("/resync")
async def resync() -> dict:
    async with _lock:
        changed = _resync()
    return {"resynced_profiles": changed}


@router.post("/activate")
async def activate() -> dict:
    """Rewire the DEFAULT profile's MCP server from current settings.

    Unused by the flattened UI. Kept so an old dashboard never 404s.
    Per-user chat traffic is routed by profile and is unaffected.
    """
    async with _lock:
        settings = envs.get_settings()
        if not settings["url"] or not settings["mcp_api_key"]:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "ยังไม่มี URL หรือ MCP API key — กรอกแล้วกดบันทึกก่อน",
            )
        rewired = envs.sync_default_profile_servers()
    return {
        "url": settings["url"],
        "rewired": rewired,
        "restart_required": True,
    }


@router.post("/test")
async def test_connection() -> dict:
    """Call ``tools/list`` against the stored URL + key."""
    settings = envs.get_settings()
    url = settings.get("url") or ""
    secret = settings.get("mcp_api_key") or ""
    if not url or not secret:
        raise HTTPException(status.HTTP_409_CONFLICT, "กรอก URL และ MCP API key แล้วกดบันทึกก่อน")

    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    ).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {secret}",
            # Cloudflare fronts public tcc-api hosts and answers 403 to urllib's
            # default "Python-urllib/x.y" agent — same URL and token return 200
            # with any ordinary agent. Without this the button reports a bad
            # URL/key for a connection that is actually fine.
            "User-Agent": "tcc-mcp-config/2.4 (Hermes dashboard)",
        },
    )

    def _call() -> dict:
        with urllib.request.urlopen(request, timeout=20) as response:
            return {
                "status": response.status,
                "body": response.read(200000).decode("utf-8", "replace"),
            }

    try:
        result = await asyncio.to_thread(_call)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            # 401 comes from the MCP server itself (McpToken middleware); 403 is
            # almost always the CDN in front of it, not the token.
            hint = (
                "MCP API key ไม่ถูกต้อง"
                if exc.code == 401
                else "ถูกบล็อกก่อนถึง MCP (มักเป็น Cloudflare/WAF หน้า URL นี้) — ไม่ใช่เรื่อง key"
            )
            return {"ok": False, "message": f"HTTP {exc.code} — {hint}"}
        return {"ok": False, "message": f"HTTP {exc.code} — ตรวจ URL หรือ MCP API key"}
    except Exception as exc:  # network / DNS / TLS
        _log.warning("tcc-mcp-config test failed: %s", exc)
        return {"ok": False, "message": f"เชื่อมต่อไม่ได้: {type(exc).__name__}"}

    text = result["body"]
    # The MCP endpoint answers as SSE ("data: {...}") or as plain JSON.
    for line in text.splitlines():
        if line.startswith("data:"):
            text = line[5:].strip()
            break
    try:
        parsed = json.loads(text)
    except Exception:
        return {"ok": False, "message": "ตอบกลับไม่ใช่ JSON ที่อ่านได้"}

    tools = (parsed.get("result") or {}).get("tools")
    if not isinstance(tools, list):
        return {"ok": False, "message": "ไม่พบรายการ tools ในคำตอบ"}

    names = sorted(
        str(t.get("name")) for t in tools if isinstance(t, dict) and t.get("name")
    )
    return {
        "ok": True,
        "tool_count": len(tools),
        "tools": names,
        "message": f"เชื่อมต่อสำเร็จ พบ {len(tools)} tools",
    }
