"""Create a Hermes profile the first time a user chats in.

Hermes resolves ``/p/<profile>/`` against a LIVE directory scan
(``hermes_cli.profiles.profiles_to_serve`` — "intentionally lightweight (a
directory scan + name validation only)"), and the ``/p/`` middleware enters that
profile's HERMES_HOME for the request. So a profile directory created at runtime
is routable on the very next request: no gateway restart, no adapter, no
registry. That is what makes one-profile-per-user practical.

What is missing is a hook to create it. Hermes' plugin API offers hooks,
middleware, tools, skills and platforms — nothing on the HTTP request path — so
this module wraps two ``ApiServerPlatform`` methods:

``_resolve_request_profile``
    Called per request, long after startup, and receives the aiohttp request.
    We let the original run; only if it rejects an unknown profile do we try to
    provision and re-resolve. Timing-independent, so it cannot be defeated by
    plugin-load ordering.

``_http_route_table``
    Adds ``POST /internal/tcc-ai-assistant/profiles/ensure``, the explicit
    endpoint ``tcc-ai-assistant`` already calls after a 404
    (``app/services/chat/hermes_relay.ts`` → ``ensureHermesProfile``). With
    auto-provisioning in place that 404 should never happen; the route is the
    honest fallback rather than a dead code path in the caller. Patched at
    plugin-discovery time, which the gateway runs before adapters connect
    (``gateway/run.py`` ``start()``).

SECURITY — why this module authenticates by itself:
    Profile resolution runs in middleware, BEFORE ``_check_auth``. An
    unauthenticated request therefore reaches provisioning first. So the bearer
    token is verified here against the *target environment's* key before
    anything is written. That also enforces environment isolation: a staging
    credential cannot create or reach a ``prod-`` profile.

Both patches are wrapped defensively. If a Hermes upgrade renames either method,
the plugin logs loudly and leaves the gateway working exactly as it did before —
unknown profiles simply 404 again, and ``tcc-ai-assistant`` degrades to its
"contact your administrator" message instead of breaking chat for everyone.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from . import environments as envs

_log = logging.getLogger("hermes.plugin.tcc-mcp-config.provisioner")

ENSURE_PATH = "/internal/tcc-ai-assistant/profiles/ensure"

_PATCHED_ATTR = "_tcc_mcp_config_patched"

# The adapter class that owns the two methods we wrap. Listed rather than
# hard-coded because getting the name wrong makes install() a silent no-op —
# chat keeps working, per-user profiles simply never appear. Current name first.
_ADAPTER_CANDIDATES = ("APIServerAdapter", "ApiServerPlatform", "APIServerPlatform")


def _find_adapter_class():
    """Return the api_server adapter class, or None outside a gateway process."""
    try:
        from gateway.platforms import api_server
    except Exception:
        # CLI / dashboard / test process — there is no gateway here to patch.
        _log.debug("gateway.platforms.api_server unavailable; provisioner idle")
        return None

    for name in _ADAPTER_CANDIDATES:
        candidate = getattr(api_server, name, None)
        if candidate is not None and hasattr(candidate, "_resolve_request_profile"):
            return candidate

    _log.error(
        "tcc-mcp-config: none of %s expose _resolve_request_profile — this "
        "Hermes version renamed the api_server adapter. Per-user profiles will "
        "NOT be created; update _ADAPTER_CANDIDATES in provisioner.py.",
        ", ".join(_ADAPTER_CANDIDATES),
    )
    return None


def _bearer(request: Any) -> str:
    header = ""
    try:
        header = request.headers.get("Authorization", "") or ""
    except Exception:
        return ""
    if header.startswith("Bearer "):
        return header[7:].strip()
    return ""


# ── patch 1: provision on an unknown /p/<profile>/ ───────────────────────

def _install_resolve_patch(platform_cls: Any) -> bool:
    from gateway.platforms.api_server import _PROFILE_REJECTED

    original = platform_cls._resolve_request_profile
    if getattr(original, _PATCHED_ATTR, False):
        return False

    def _resolve_request_profile(self, request):
        resolved = original(self, request)

        name = ""
        try:
            name = (request.match_info.get("profile") or "").strip()
        except Exception:
            return resolved
        if not envs.PROFILE_RE.match(name):
            return resolved  # not ours — keep hermes' behaviour

        if resolved is not _PROFILE_REJECTED:
            # Hermes accepted the name, which only means the DIRECTORY exists.
            # A profile deleted from the dashboard leaves a skeleton with no
            # .env behind, and that authenticates as nothing — every request
            # 401s and nothing ever repairs it, because provisioning used to
            # run only on the 404 path. Repair it here instead.
            try:
                if envs.is_complete(envs.profile_dir(name)):
                    return resolved
                ok, detail = envs.ensure_profile(name, bearer=_bearer(request))
                if ok and detail == "repaired":
                    _log.warning("repaired incomplete profile '%s'", name)
                elif not ok:
                    _log.warning("could not repair '%s': %s", name, detail)
            except Exception:
                _log.exception("repair check failed for '%s'", name)
            return resolved

        try:
            ok, detail = envs.ensure_profile(name, bearer=_bearer(request))
        except Exception:
            _log.exception("provisioning '%s' failed", name)
            return resolved
        if not ok:
            # Never echo the reason to the caller — an unauthenticated probe
            # would otherwise learn which environments exist and which profile
            # names are well-formed. Hermes' generic 404 stands.
            _log.warning("refused to provision '%s': %s", name, detail)
            return resolved

        if detail == "created":
            _log.info("provisioned profile '%s' on first chat", name)
        # Re-resolve so the profile passes hermes' own validation this time.
        return original(self, request)

    setattr(_resolve_request_profile, _PATCHED_ATTR, True)
    platform_cls._resolve_request_profile = _resolve_request_profile
    return True


# ── patch 2: the explicit ensure endpoint ────────────────────────────────

def _make_ensure_handler():
    from aiohttp import web

    async def handle_ensure(request):
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        env = envs.normalize_env(payload.get("environment"))
        principal_type = str(payload.get("type") or "").strip().lower()
        raw_id = str(payload.get("id") or "").strip()
        raw_store = payload.get("store_id")
        raw_store = "" if raw_store in (None, "") else str(raw_store).strip()

        if not env or principal_type not in ("staff", "user") or not raw_id.isdigit():
            return web.json_response({"error": "invalid request"}, status=400)
        if raw_store and not raw_store.isdigit():
            return web.json_response({"error": "invalid request"}, status=400)

        name = f"{env}-{principal_type}-{int(raw_id)}"
        if principal_type == "user" and raw_store:
            name += f"-store-{int(raw_store)}"
        try:
            ok, detail = envs.ensure_profile(name, bearer=_bearer(request))
        except Exception:
            _log.exception("ensure endpoint failed for '%s'", name)
            return web.json_response({"error": "provisioning failed"}, status=500)

        if not ok:
            status = 401 if detail == "unauthorized" else 400
            # Same reasoning as above: no detail on the 401 path.
            body = {"error": "unauthorized" if status == 401 else detail}
            return web.json_response(body, status=status)

        return web.json_response({"profile": name, "created": detail == "created"})

    return handle_ensure


def _install_route_patch(platform_cls: Any) -> bool:
    original = platform_cls._http_route_table
    if getattr(original, _PATCHED_ATTR, False):
        return False

    def _http_route_table(self):
        routes = original(self)
        try:
            routes = list(routes)
            routes.append(("POST", ENSURE_PATH, _make_ensure_handler()))
        except Exception:
            _log.exception("could not append the ensure route")
        return routes

    setattr(_http_route_table, _PATCHED_ATTR, True)
    platform_cls._http_route_table = _http_route_table
    return True


# ── entry point ──────────────────────────────────────────────────────────

def install() -> None:
    """Patch the API-server adapter. Safe to call more than once."""
    adapter_cls = _find_adapter_class()
    if adapter_cls is None:
        return

    try:
        resolved = _install_resolve_patch(adapter_cls)
    except Exception:
        _log.exception(
            "could not install profile auto-provisioning — unknown profiles "
            "will 404 and per-user memory will not be created"
        )
        resolved = False

    try:
        routed = _install_route_patch(adapter_cls)
    except Exception:
        _log.exception("could not install the %s route", ENSURE_PATH)
        routed = False

    if resolved or routed:
        _log.info(
            "tcc-mcp-config: profile provisioning armed (auto=%s, endpoint=%s)",
            resolved,
            routed,
        )
