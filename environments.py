"""Per-environment TCC settings + per-user profile provisioning.

One Hermes gateway serves THREE environments at once (local / staging /
production) and ONE Hermes profile per chat user, so every user keeps their own
``memories/MEMORY.md``. Both facts are encoded in the profile name::

    <env>-api-<staff|user>-<id>          e.g. stg-api-user-2520153

``tcc-ai-assistant`` builds that name (``app/services/chat/profile_routing.ts``)
and calls ``POST /p/<name>/v1/chat/completions``. The gateway's ``/p/`` middleware
enters that profile's HERMES_HOME for the request, which is what makes memory,
``config.yaml`` (→ the MCP URL for that environment) and ``.env`` per-user.

Why settings live in the DEFAULT profile's ``.env``:
    Under multiplex, ``agent.secret_scope`` is fail-closed — a profile only ever
    sees its own ``.env``. So each provisioned profile gets a *materialized copy*
    of the values for its environment rather than a reference. This module is the
    single writer of both sides, so the dashboard and the provisioner can never
    disagree about a key name.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple

# ── environments ─────────────────────────────────────────────────────────
# Short codes are what appear in profile names (kept short: the whole profile
# name must fit hermes' 64-char _PROFILE_ID_RE).
ENVIRONMENTS = ("local", "stg", "prod")

# tcc-ai-assistant sends the long form; the dashboard shows the long form.
_ENV_ALIASES = {
    "local": "local",
    "dev": "local",
    "development": "local",
    "stg": "stg",
    "staging": "stg",
    "prod": "prod",
    "production": "prod",
}

ENV_LABELS = {"local": "Local", "stg": "Staging", "prod": "Production"}

# The ONLY profile names this plugin will ever create. Anything else is refused,
# so a caller cannot steer directory creation with a crafted URL.
#
#   <env>-staff-<id>
#   <env>-user-<id>[-store-<storeId>]
#
# The store is part of the name so one organizer managing several stores keeps
# separate memory per store, matching how tcc-ai-assistant keys transcripts.
PROFILE_RE = re.compile(
    r"^(local|stg|prod)-(?:staff-[0-9]{1,12}|user-[0-9]{1,12}(?:-store-[0-9]{1,12})?)$"
)

# Bounds the blast radius of a runaway/hostile provisioning loop.
MAX_PROFILES = 5000

# The MCP server name used by the DEFAULT profile (direct, non-/p/ calls and
# `hermes mcp test`). Hermes exposes tools as mcp__<sanitized name>__<tool>.
MCP_SERVER_NAME = "tcc-api"


def mcp_server_name(env: str) -> str:
    """Per-environment MCP server name — ``tcc-api-stg``, ``tcc-api-prod``, …

    Each environment MUST use a DISTINCT name. ``tools.mcp_tool`` keeps its
    connected servers in a process-global dict keyed by server NAME alone
    (``register_mcp_servers``: ``if k not in _servers`` → "already connected,
    reuse"). One gateway process serves every profile, so if all profiles
    called their server ``tcc-api``, whichever environment connected FIRST
    would own that name and every other environment's turns would silently be
    answered by it — staging users reading local data, or worse. Config is
    per-profile; the connection pool is not.

    Observed directly: a staging profile with the correct staging URL and key
    returned local's (empty) event list until the names were split.
    """
    return f"{MCP_SERVER_NAME}-{env}"

# Provider credentials copied from the default profile into each new profile.
# A WHITELIST on purpose: copying the whole .env would drag platform tokens
# (TELEGRAM_*, DISCORD_*, SLACK_*) along, and gateway config resolves platform
# enablement from env vars — every user profile would try to poll the same bot
# token and collide. Only model-provider credentials belong here.
_PROVIDER_KEY_RE = re.compile(
    r"^(OPENAI|ANTHROPIC|OPENROUTER|NOUS|GROQ|DEEPSEEK|XAI|GEMINI|GOOGLE|"
    r"MISTRAL|TOGETHER|FIREWORKS|CEREBRAS|AZURE_OPENAI|OLLAMA|LITELLM|VLLM)"
    r"_[A-Z0-9_]+$"
)


def normalize_env(raw: object) -> Optional[str]:
    """Map any accepted spelling to its short code, or None if unknown."""
    return _ENV_ALIASES.get(str(raw or "").strip().lower())


# ── settings keys (default profile .env) ─────────────────────────────────

def key_mcp_url(env: str) -> str:
    return f"TCC_MCP_URL_{env.upper()}"


def key_mcp_api_key(env: str) -> str:
    return f"TCC_MCP_KEY_{env.upper()}"


def key_gateway_key(env: str) -> str:
    """The API_SERVER_KEY every profile of this environment authenticates with.

    All profiles in one environment deliberately SHARE this value: the caller is
    tcc-ai-assistant (a trusted backend holding one HERMES_API_KEY per
    deployment), never the end user. Per-user isolation comes from the profile
    in the URL and the principal in X-Hermes-Session-Key — not from this key.
    Sharing it is what keeps provisioning free of any key-distribution step.
    """
    return f"TCC_GATEWAY_KEY_{env.upper()}"


# ── paths ────────────────────────────────────────────────────────────────

def default_home() -> Path:
    """The DEFAULT profile's HERMES_HOME.

    Deliberately not ``get_hermes_home()``: during a ``/p/<profile>/`` request
    that returns the *scoped* profile home, and settings live in the default
    profile only.
    """
    try:
        from hermes_cli.profiles import _get_default_hermes_home

        return Path(_get_default_hermes_home())
    except Exception:
        env_home = os.environ.get("HERMES_HOME")
        return Path(env_home) if env_home else Path.home() / ".hermes"


def profiles_root() -> Path:
    return default_home() / "profiles"


def profile_dir(name: str) -> Path:
    return profiles_root() / name


# ── .env read/write ──────────────────────────────────────────────────────

def read_env_file(path: Path) -> Dict[str, str]:
    """Parse a dotenv file. Never touches os.environ."""
    values: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return values
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env_file(path: Path, values: Dict[str, str], header: str = "") -> None:
    """Atomically rewrite a dotenv file at 0600.

    Written to a temp file in the same directory then ``os.replace``d, so a
    crash mid-write can never leave a half-written credential file that would
    silently fail closed on the next read.
    """
    lines = [header.rstrip("\n")] if header else []
    lines.extend(f"{k}={v}" for k, v in values.items() if v is not None)
    body = "\n".join(lines) + "\n"

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    try:
        os.write(fd, body.encode("utf-8"))
        os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _default_env_path() -> Path:
    return default_home() / ".env"


def get_settings(env: str) -> Dict[str, str]:
    """Return {url, mcp_api_key, gateway_key} for one environment."""
    values = read_env_file(_default_env_path())
    return {
        "url": values.get(key_mcp_url(env), ""),
        "mcp_api_key": values.get(key_mcp_api_key(env), ""),
        "gateway_key": values.get(key_gateway_key(env), ""),
    }


def save_settings(
    env: str,
    *,
    url: Optional[str] = None,
    mcp_api_key: Optional[str] = None,
    gateway_key: Optional[str] = None,
) -> None:
    """Update one environment's settings. ``None`` leaves a field untouched."""
    path = _default_env_path()
    values = read_env_file(path)
    if url is not None:
        values[key_mcp_url(env)] = url.strip()
    if mcp_api_key is not None:
        values[key_mcp_api_key(env)] = mcp_api_key.strip()
    if gateway_key is not None:
        values[key_gateway_key(env)] = gateway_key.strip()
    write_env_file(path, values, header="# Hermes profile .env")


def check_gateway_key(env: str, presented: str) -> bool:
    """Constant-time check of a bearer token against one environment's key.

    This is what stops a staging deployment from provisioning (or reaching)
    production profiles, and what stops an unauthenticated caller from creating
    directories: profile resolution runs BEFORE the api_server's own auth check,
    so the provisioner must authenticate the request itself.
    """
    expected = get_settings(env).get("gateway_key") or ""
    if not expected or len(expected) < 16 or not presented:
        return False
    return hmac.compare_digest(str(presented).encode(), expected.encode())


# ── profile materialization ──────────────────────────────────────────────

def _model_block() -> str:
    """Reuse the default profile's model choice so replies are identical."""
    try:
        text = (default_home() / "config.yaml").read_text(encoding="utf-8")
        match = re.search(r"^model:\n(?:[ \t]+.*\n)+", text, re.M)
        if match:
            return match.group(0)
    except OSError:
        pass
    return "model:\n  default: gpt-5.4-mini\n  provider: openai-api\n"


def _yaml_single_quoted(value: str) -> str:
    """Render a scalar as a YAML single-quoted string ('' escapes a quote)."""
    return "'" + str(value or "").replace("'", "''") + "'"


def _profile_config_yaml(env: str) -> str:
    """config.yaml for a provisioned profile.

    ``platforms.api_server.enabled: false`` must be stated EXPLICITLY, not just
    omitted: gateway config resolves api_server as enabled from the inherited
    process env, and the multiplexer then refuses the profile with "enables
    port-binding platform(s) api_server". Port-binding platforms belong to the
    default profile; this profile is reached through the /p/<name>/ prefix.

    ``platform_toolsets.api_server`` must list ``memory`` or the agent has no
    memory tool on the API-server path and can never write anything down — the
    whole point of one profile per user. MCP tools are not gated by this list.

    The MCP url and Authorization header are written as LITERALS, deliberately,
    even though a ``${VAR}`` placeholder would be tidier. ``hermes_cli.config.
    load_config()`` honours the scoped HERMES_HOME (it does read this file) but
    resolves ``${VAR}`` against ``os.environ`` — the DEFAULT profile's values —
    before the profile-scoped interpolation in ``tools.mcp_tool`` ever sees a
    placeholder. With placeholders here, every profile silently talks to
    whichever environment the default profile points at, which is precisely the
    staging/production mix-up this plugin exists to prevent. Verified on
    Hermes v0.20.0. Cost of literals: rotating a key rewrites every profile —
    which ``resync_profile`` already does on every dashboard save.
    """
    settings = get_settings(env)
    return (
        _model_block()
        + "\n"
        "platforms:\n"
        "  api_server:\n"
        "    enabled: false\n"
        "\n"
        "mcp_servers:\n"
        f"  {mcp_server_name(env)}:\n"
        f"    url: {_yaml_single_quoted(settings['url'])}\n"
        "    headers:\n"
        "      Authorization: "
        f"{_yaml_single_quoted('Bearer ' + settings['mcp_api_key'])}\n"
        "\n"
        "platform_toolsets:\n"
        "  api_server:\n"
        "    - memory\n"
        "\n"
        "memory:\n"
        "  memory_enabled: true\n"
        "  user_profile_enabled: true\n"
    )


def _write_profile_config(target: Path, env: str) -> None:
    """Write config.yaml at 0600 — it carries the MCP bearer token verbatim."""
    path = target / "config.yaml"
    path.write_text(_profile_config_yaml(env), encoding="utf-8")
    os.chmod(path, 0o600)


def _profile_env_values(env: str) -> Dict[str, str]:
    settings = get_settings(env)
    values = {
        "API_SERVER_KEY": settings["gateway_key"],
        "TCC_ACTIVE_MCP_ENV": env,
        "TCC_ACTIVE_MCP_URL": settings["url"],
        "TCC_ACTIVE_MCP_API_KEY": settings["mcp_api_key"],
    }
    for name, value in read_env_file(_default_env_path()).items():
        if value and _PROVIDER_KEY_RE.match(name):
            values[name] = value
    return values


def _materialize(target: Path, env: str) -> None:
    """Build a complete profile in a temp dir, then move it into place.

    Built out-of-place so a concurrent first-chat from the same user can never
    observe a half-written profile: the rename is atomic, and the loser of the
    race just finds the directory already there.
    """
    staging = Path(
        tempfile.mkdtemp(dir=str(target.parent), prefix=f".tmp-{target.name}-")
    )
    try:
        _write_profile_config(staging, env)
        write_env_file(
            staging / ".env",
            _profile_env_values(env),
            header="# Managed by tcc-mcp-config — do not edit by hand.",
        )
        (staging / "memories").mkdir(exist_ok=True)
        os.replace(str(staging), str(target))
    except OSError:
        # Either a peer won the race (target now exists) or the move failed.
        # Both leave `target` authoritative; clean up our staging copy.
        shutil.rmtree(staging, ignore_errors=True)
        if not target.is_dir():
            raise


def is_complete(target: Path) -> bool:
    """Does this directory hold a profile Hermes can actually authenticate?

    A directory can exist without being usable: deleting a profile from the
    dashboard and then letting any turn run under its scope leaves Hermes' own
    skeleton behind (``cron/``, ``sessions/``, ``SOUL.md``) with **no** ``.env``.
    Hermes then routes ``/p/<name>/`` happily — the directory is there — but
    ``_expected_api_key`` finds no ``API_SERVER_KEY`` and every request 401s
    forever. Existence alone is not enough to skip provisioning.
    """
    return (target / "config.yaml").is_file() and (target / ".env").is_file()


def ensure_profile(name: str, *, bearer: str) -> Tuple[bool, str]:
    """Create — or repair — the profile for ``name`` if allowed.

    Returns ``(ok, detail)`` where detail is ``created`` / ``repaired`` /
    ``exists`` or a short refusal reason. Never raises for an ordinary refusal —
    callers use this on the request path.
    """
    match = PROFILE_RE.match(name or "")
    if not match:
        return False, "invalid profile name"
    env = match.group(1)

    settings = get_settings(env)
    if not settings["gateway_key"]:
        return False, f"environment {env} has no gateway key configured"
    if not settings["url"] or not settings["mcp_api_key"]:
        return False, f"environment {env} is not configured"
    if not check_gateway_key(env, bearer):
        return False, "unauthorized"

    target = profile_dir(name)
    if target.is_dir():
        if is_complete(target):
            return True, "exists"
        # Half-deleted or skeleton-only: rewrite the generated files in place.
        # Memory and sessions, if any survived, are left untouched.
        _write_profile_config(target, env)
        write_env_file(
            target / ".env",
            _profile_env_values(env),
            header="# Managed by tcc-mcp-config — do not edit by hand.",
        )
        (target / "memories").mkdir(exist_ok=True)
        return True, "repaired"

    root = profiles_root()
    root.mkdir(parents=True, exist_ok=True)
    try:
        if sum(1 for entry in root.iterdir() if entry.is_dir()) >= MAX_PROFILES:
            return False, "profile limit reached"
    except OSError:
        pass

    _materialize(target, env)
    return True, "created"


def resync_profile(name: str) -> bool:
    """Rewrite an existing profile's config/.env from current settings.

    Used after the operator rotates a key or repoints an environment. Memory and
    sessions are untouched — only the generated files are replaced.
    """
    match = PROFILE_RE.match(name or "")
    target = profile_dir(name)
    if not match or not target.is_dir():
        return False
    env = match.group(1)
    _write_profile_config(target, env)
    write_env_file(
        target / ".env",
        _profile_env_values(env),
        header="# Managed by tcc-mcp-config — do not edit by hand.",
    )
    return True


def _live_state_path() -> Path:
    return default_home() / ".tcc-mcp-config-live.json"


def _fingerprint(env: str) -> str:
    """Identify an environment's MCP wiring without storing the key."""
    settings = get_settings(env)
    if not settings["url"] or not settings["mcp_api_key"]:
        return ""
    raw = settings["url"] + "\0" + settings["mcp_api_key"]
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mark_live() -> None:
    """Record what the gateway actually connected with, at gateway startup.

    Hermes connects MCP servers ONCE per process. Everything the dashboard
    writes afterwards is only a promise about the NEXT restart, so without this
    marker the UI cannot tell "configured" from "actually serving traffic" — and
    it would cheerfully show a freshly-configured environment as ready while
    every chat turn in it has no tools at all.
    """
    state = {env: _fingerprint(env) for env in ENVIRONMENTS}
    try:
        _live_state_path().write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def live_environments() -> Dict[str, bool]:
    """Per environment: is the CURRENT config the one the gateway is running?"""
    try:
        state = json.loads(_live_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    result = {}
    for env in ENVIRONMENTS:
        current = _fingerprint(env)
        result[env] = bool(current) and state.get(env) == current
    return result


def sync_default_profile_servers() -> bool:
    """Declare every configured environment's MCP server on the DEFAULT profile.

    This is what actually gets the connections made. Hermes runs MCP discovery
    ONCE, at gateway startup, under the default profile — ``discover_mcp_tools``
    is never called again under a ``/p/<profile>/`` runtime scope, so a server
    that only ever appears in a user profile's config.yaml is never connected
    and that user's turn has no tools at all.

    A per-profile turn still only *sees* the servers its own config.yaml names,
    so declaring all three here does not leak production tools into a staging
    user's turn: the default profile owns the connections, each user profile
    selects exactly one of them by name.

    Returns True when the file changed. Idempotent — called on every gateway
    start and after every dashboard save.
    """
    path = default_home() / "config.yaml"
    if not path.exists():
        return False

    from ruamel.yaml import YAML

    handler = YAML()  # round-trip: keeps comments, quotes and ordering
    handler.preserve_quotes = True
    handler.width = 4096
    with path.open("r", encoding="utf-8") as handle:
        config = handler.load(handle) or {}

    servers = config.get("mcp_servers")
    if servers is None:
        config["mcp_servers"] = servers = {}

    changed = False
    for env in ENVIRONMENTS:
        settings = get_settings(env)
        name = mcp_server_name(env)
        if not settings["url"] or not settings["mcp_api_key"]:
            # Never leave a half-configured server behind: it would fail to
            # connect on every startup and sit in the reconnect backoff.
            if name in servers:
                del servers[name]
                changed = True
            continue
        want_auth = f"Bearer {settings['mcp_api_key']}"
        entry = servers.get(name)
        if not isinstance(entry, dict):
            servers[name] = entry = {}
            changed = True
        if entry.get("url") != settings["url"]:
            entry["url"] = settings["url"]
            changed = True
        headers = entry.get("headers")
        if not isinstance(headers, dict):
            entry["headers"] = headers = {}
            changed = True
        if headers.get("Authorization") != want_auth:
            headers["Authorization"] = want_auth
            changed = True

    if not changed:
        return False

    backup = path.with_suffix(path.suffix + ".bak-tcc-mcp-config")
    if not backup.exists():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix="config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handler.dump(config, handle)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def list_profiles() -> Dict[str, int]:
    """Count provisioned per-user profiles per environment (for the dashboard)."""
    counts = {env: 0 for env in ENVIRONMENTS}
    try:
        for entry in profiles_root().iterdir():
            if not entry.is_dir():
                continue
            match = PROFILE_RE.match(entry.name)
            if match:
                counts[match.group(1)] += 1
    except OSError:
        pass
    return counts
