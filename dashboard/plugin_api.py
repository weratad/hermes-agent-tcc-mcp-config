"""Backend for the TCC MCP Config dashboard tab.

Holds the MCP URL, MCP API key and gateway key for ``local``, ``stg`` and
``prod`` — all three live at once. There is no "switch to production" step:
which environment a chat turn uses is decided by the profile in the request URL
(``/p/<env>-api-<type>-<id>/``), so the three configurations are simply
materialized into the profiles that belong to each of them.

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
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

router = APIRouter()
_log = logging.getLogger("hermes.plugin.tcc-mcp-config")
_lock = asyncio.Lock()


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
ACTIVE_URL = "TCC_ACTIVE_MCP_URL"
ACTIVE_KEY = "TCC_ACTIVE_MCP_API_KEY"
ACTIVE_ENV = "TCC_ACTIVE_MCP_ENV"

# Keys written by v1 of this plugin, imported once so an upgrade keeps working.
_LEGACY = {
    "stg": ("TCC_STAGING_MCP_URL", "TCC_STAGING_MCP_API_KEY", "MCP_TCC_MCP_API_KEY"),
    "prod": ("TCC_PRODUCTION_MCP_URL", "TCC_PRODUCTION_MCP_API_KEY", None),
}


class SaveEnvironment(BaseModel):
    environment: str
    url: Optional[str] = None
    mcp_api_key: Optional[str] = None
    gateway_key: Optional[str] = None


class SelectEnvironment(BaseModel):
    environment: str


def _check_env(environment: str) -> str:
    normalized = envs.normalize_env(environment)
    if not normalized:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unknown environment: {environment}")
    return normalized


def _check_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "url is required")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "url must start with http:// or https://")
    if any(char in url for char in "\r\n"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "url contains invalid characters")
    return url


def _env_path() -> Path:
    return envs.default_home() / ".env"


def _read_env() -> Dict[str, str]:
    return envs.read_env_file(_env_path())


def _write_env(updates: Dict[str, str]) -> None:
    """Upsert keys in .env, preserving every other line, atomically at 0600."""
    path = _env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    out = []
    for line in lines:
        stripped = line.strip()
        name = stripped.partition("=")[0].strip() if "=" in stripped else ""
        if name and name in remaining and not stripped.startswith("#"):
            out.append(f"{name}={remaining.pop(name)}")
        else:
            out.append(line)
    out.extend(f"{name}={value}" for name, value in remaining.items())

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(out) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    os.environ.update(updates)


def _migrate_legacy() -> bool:
    """Copy v1 / pre-plugin keys into the v2 slots. Runs once, never overwrites."""
    values = _read_env()
    updates: Dict[str, str] = {}
    for env, (legacy_url, legacy_key, fallback_key) in _LEGACY.items():
        if not values.get(envs.key_mcp_url(env)) and values.get(legacy_url):
            updates[envs.key_mcp_url(env)] = values[legacy_url]
        if not values.get(envs.key_mcp_api_key(env)):
            carried = values.get(legacy_key) or (values.get(fallback_key) if fallback_key else "")
            if carried:
                updates[envs.key_mcp_api_key(env)] = carried

    # Staging inherits the default profile's API_SERVER_KEY so the tcc-ai-assistant
    # deployment that already holds it keeps working across this upgrade without
    # anyone editing HERMES_API_KEY. Production must be given its own key —
    # see the duplicate check in save_settings().
    if not values.get(envs.key_gateway_key("stg")) and values.get("API_SERVER_KEY"):
        updates[envs.key_gateway_key("stg")] = values["API_SERVER_KEY"]

    if updates:
        _write_env(updates)
        _log.info("tcc-mcp-config: migrated %d legacy setting(s)", len(updates))
    return bool(updates)


def _mask(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    return f"…{value[-4:]}" if len(value) > 4 else "…"


def _describe(
    values: Dict[str, str], env: str, counts: Dict[str, int], live: Dict[str, bool]
) -> dict:
    url = values.get(envs.key_mcp_url(env), "")
    mcp_key = values.get(envs.key_mcp_api_key(env), "")
    gateway_key = values.get(envs.key_gateway_key(env), "")
    configured = bool(url and mcp_key and len(gateway_key) >= 16)
    return {
        "environment": env,
        "label": envs.ENV_LABELS[env],
        "url": url,
        "mcp_key_set": bool(mcp_key),
        "mcp_key_hint": _mask(mcp_key),
        "gateway_key_set": bool(gateway_key),
        "gateway_key_hint": _mask(gateway_key),
        "gateway_key_weak": bool(gateway_key) and len(gateway_key) < 16,
        "configured": configured,
        # Hermes connects MCP servers once per gateway process, so "saved" and
        # "serving traffic" are different states. Reporting only the first would
        # show an environment as ready while its chats have no tools at all.
        "live": configured and live.get(env, False),
        "mcp_server_name": envs.mcp_server_name(env),
        "profiles": counts.get(env, 0),
        "profile_example": f"{env}-staff-123 / {env}-user-123-store-456",
    }


@router.get("/profiles")
async def list_profiles_detail(
    env: str = "",
    q: str = "",
    page: int = 1,
    page_size: int = 10,
) -> dict:
    """List provisioned per-user profiles — filterable by environment + search, paginated.

    Powers the dashboard's Profiles panel. Read-only: a directory scan of
    ``profiles/`` matched against ``PROFILE_RE`` (same source list_profiles()
    counts from), so it never touches gateway state.
    """
    valid_env = env if env in envs.ENVIRONMENTS else ""
    needle = (q or "").strip().lower()
    rows = []
    try:
        for entry in envs.profiles_root().iterdir():
            if not entry.is_dir():
                continue
            match = envs.PROFILE_RE.match(entry.name)
            if not match:
                continue
            e = match.group(1)
            if valid_env and e != valid_env:
                continue
            if needle and needle not in entry.name.lower():
                continue
            rest = entry.name[len(e) + 1 :]
            parts = rest.split("-")
            ptype = parts[0] if parts else ""
            pid = parts[1] if len(parts) > 1 else ""
            store = parts[3] if len(parts) > 3 and parts[2] == "store" else ""
            try:
                mtime = int(entry.stat().st_mtime)
            except OSError:
                mtime = 0
            rows.append(
                {
                    "name": entry.name,
                    "environment": e,
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
        migrated = _migrate_legacy()
        values = _read_env()
    counts = envs.list_profiles()
    live = envs.live_environments()
    active = values.get(ACTIVE_ENV, "")

    # Two environments sharing one gateway key would let the staging backend
    # provision and reach production profiles — surface it rather than silently
    # accepting it (save_settings refuses to create the situation).
    keys = {env: values.get(envs.key_gateway_key(env), "") for env in envs.ENVIRONMENTS}
    duplicates = sorted(
        {
            env
            for env, key in keys.items()
            if key and sum(1 for other in keys.values() if other == key) > 1
        }
    )

    described = [_describe(values, env, counts, live) for env in envs.ENVIRONMENTS]
    return {
        "mcp_server_name": MCP_SERVER_NAME,
        "default_active": envs.normalize_env(active),
        "environments": described,
        "migrated": migrated,
        "duplicate_gateway_keys": duplicates,
        # Any environment that is set up but not yet the one this gateway
        # process connected with. Until it restarts, those chats have no tools.
        "restart_required": [
            item["environment"] for item in described if item["configured"] and not item["live"]
        ],
        "restart_command": "docker exec -d hermes-box hermes gateway run",
    }


@router.put("/settings")
async def save_settings(body: SaveEnvironment) -> dict:
    env = _check_env(body.environment)

    async with _lock:
        values = _read_env()
        updates: Dict[str, str] = {}

        if body.url is not None:
            updates[envs.key_mcp_url(env)] = _check_url(body.url)
        if body.mcp_api_key and body.mcp_api_key.strip():
            updates[envs.key_mcp_api_key(env)] = body.mcp_api_key.strip()

        gateway_key = (body.gateway_key or "").strip()
        if gateway_key:
            if len(gateway_key) < 16:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "gateway key ต้องยาวอย่างน้อย 16 ตัวอักษร",
                )
            clashes = [
                other
                for other in envs.ENVIRONMENTS
                if other != env and values.get(envs.key_gateway_key(other)) == gateway_key
            ]
            if clashes:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"gateway key นี้ถูกใช้กับ {', '.join(envs.ENV_LABELS[c] for c in clashes)} อยู่แล้ว "
                    "— แต่ละสภาพแวดล้อมต้องใช้คีย์คนละตัว",
                )
            updates[envs.key_gateway_key(env)] = gateway_key

        if not updates:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "ไม่มีอะไรให้บันทึก")

        # Keep the default profile's pointers in sync when editing the
        # environment it is aimed at (used by `hermes mcp test` and any
        # non-/p/ call).
        if values.get(ACTIVE_ENV) == env:
            if envs.key_mcp_url(env) in updates:
                updates[ACTIVE_URL] = updates[envs.key_mcp_url(env)]
            if envs.key_mcp_api_key(env) in updates:
                updates[ACTIVE_KEY] = updates[envs.key_mcp_api_key(env)]

        _write_env(updates)
        values = _read_env()
        # Push the new values into every profile of this environment so existing
        # users pick them up without being re-created.
        resynced = _resync(env)
        # Declare this environment's MCP server on the DEFAULT profile — that is
        # the only config Hermes reads when it connects MCP servers at startup.
        # Without this a newly configured environment would never be connected
        # at all, not even after a restart.
        envs.sync_default_profile_servers()

    counts = envs.list_profiles()
    live = envs.live_environments()
    described = _describe(values, env, counts, live)
    return {
        "saved": True,
        "resynced_profiles": resynced,
        "restart_required": described["configured"] and not described["live"],
        **described,
    }


def _resync(env: str) -> int:
    changed = 0
    try:
        for entry in envs.profiles_root().iterdir():
            if not entry.is_dir():
                continue
            match = envs.PROFILE_RE.match(entry.name)
            if match and match.group(1) == env and envs.resync_profile(entry.name):
                changed += 1
    except OSError:
        _log.warning("tcc-mcp-config: could not resync %s profiles", env, exc_info=True)
    return changed


@router.post("/resync")
async def resync(body: SelectEnvironment) -> dict:
    env = _check_env(body.environment)
    async with _lock:
        changed = _resync(env)
    return {"environment": env, "resynced_profiles": changed}


@router.post("/activate")
async def activate(body: SelectEnvironment) -> dict:
    """Aim the DEFAULT profile at one environment.

    Only affects direct (non-``/p/``) calls and ``hermes mcp test`` — per-user
    chat traffic is routed by profile and is unaffected by this setting.
    """
    env = _check_env(body.environment)
    async with _lock:
        values = _read_env()
        url = values.get(envs.key_mcp_url(env), "")
        secret = values.get(envs.key_mcp_api_key(env), "")
        if not url or not secret:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{envs.ENV_LABELS[env]} ยังไม่มี URL หรือ MCP API key — กรอกแล้วกดบันทึกก่อน",
            )
        _write_env({ACTIVE_URL: url, ACTIVE_KEY: secret, ACTIVE_ENV: env})
        rewired = _ensure_config_wired()
    return {"active": env, "url": url, "rewired": rewired, "restart_required": True}


# ── default-profile config wiring ────────────────────────────────────────

def _config_path() -> Path:
    return envs.default_home() / "config.yaml"


def _ensure_config_wired() -> bool:
    """Point ``mcp_servers.<name>`` at the ``${TCC_ACTIVE_*}`` placeholders.

    ``mcp_tool._interpolate_env_vars`` runs over the whole server config dict,
    not just headers, so ``url`` accepts ``${VAR}`` too. Wiring the file once
    means every later change is a plain .env write. Idempotent; backs the file
    up before its first modification.
    """
    path = _config_path()
    if not path.exists():
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "config.yaml not found")

    from ruamel.yaml import YAML

    handler = YAML()  # round-trip: preserves comments, quotes and ordering
    handler.preserve_quotes = True
    handler.width = 4096
    with path.open("r", encoding="utf-8") as handle:
        config = handler.load(handle) or {}

    servers = config.get("mcp_servers")
    if servers is None:
        config["mcp_servers"] = servers = {}
    entry = servers.get(MCP_SERVER_NAME)
    if entry is None:
        servers[MCP_SERVER_NAME] = entry = {}
    headers = entry.get("headers")
    if headers is None:
        entry["headers"] = headers = {}

    want_url = "${" + ACTIVE_URL + "}"
    want_auth = "Bearer ${" + ACTIVE_KEY + "}"
    if entry.get("url") == want_url and headers.get("Authorization") == want_auth:
        return False

    entry["url"] = want_url
    headers["Authorization"] = want_auth

    backup = path.with_suffix(path.suffix + ".bak-tcc-mcp-config")
    if not backup.exists():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handler.dump(config, handle)
        os.replace(tmp, path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    _log.info("tcc-mcp-config: wired mcp_servers.%s to env placeholders", MCP_SERVER_NAME)
    return True


# ── connectivity probe ───────────────────────────────────────────────────

@router.post("/test")
async def test_connection(body: SelectEnvironment) -> dict:
    """Call ``tools/list`` against one environment's stored URL + key."""
    env = _check_env(body.environment)
    values = _read_env()
    url = values.get(envs.key_mcp_url(env), "")
    secret = values.get(envs.key_mcp_api_key(env), "")
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
            # Cloudflare fronts api.tcc-stg.com and answers 403 to urllib's
            # default "Python-urllib/x.y" agent — same URL and token return 200
            # with any ordinary agent. Without this the button reports a bad
            # URL/key for a connection that is actually fine.
            "User-Agent": "tcc-mcp-config/2.2 (Hermes dashboard)",
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
        _log.warning("tcc-mcp-config test failed for %s: %s", env, exc)
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
