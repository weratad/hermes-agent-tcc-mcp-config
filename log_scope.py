"""Keep gateway logging out of the per-user profile directories.

``hermes_logging.setup_logging()`` is called once per agent turn
(``agent/agent_init.py`` → ``setup_logging(hermes_home=_ra()._hermes_home)``),
and under a ``/p/<profile>/`` request that home is the *user's* profile. Two
things follow, both wrong for a multiplexed gateway:

1. It does ``log_dir.mkdir(parents=True, exist_ok=True)`` near the top, ~60
   lines BEFORE the ``if _logging_initialized and not force: return`` guard. So
   every turn creates ``profiles/<user>/logs/`` even though it then no-ops —
   and it keeps recreating that directory after you delete it, which looks
   exactly like a phantom profile being provisioned.
2. Only the FIRST call actually installs handlers, so every later turn's output
   lands in whichever profile happened to chat first. One user's log file ends
   up holding everybody's turns.

Per-profile log files cannot work correctly here anyway: one process serves all
profiles concurrently, so a shared root logger would have to swap handlers
mid-turn and would interleave regardless. The gateway's logs belong to the
gateway. This patch pins them to the DEFAULT profile whenever the requested home
is one of our auto-created per-user profiles, and leaves every other caller
(CLI, dashboard, a genuinely separate profile) untouched.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import environments as envs

_log = logging.getLogger("hermes.plugin.tcc-mcp-config.log-scope")

_PATCHED_ATTR = "_tcc_mcp_config_log_scope_patched"


def _is_user_profile_home(home) -> bool:
    """True only for ``<hermes home>/profiles/<name>`` that we provisioned."""
    try:
        path = Path(home)
        return path.parent == envs.profiles_root() and bool(envs.PROFILE_RE.match(path.name))
    except Exception:
        return False


def install() -> None:
    """Redirect per-user-profile logging to the default profile. Idempotent."""
    try:
        import hermes_logging
    except Exception:
        _log.debug("hermes_logging unavailable; log scoping idle")
        return

    original = getattr(hermes_logging, "setup_logging", None)
    if original is None:
        _log.error(
            "tcc-mcp-config: hermes_logging.setup_logging is gone — per-user "
            "profiles will keep collecting stray logs/ directories"
        )
        return
    if getattr(original, _PATCHED_ATTR, False):
        return

    def setup_logging(*, hermes_home=None, **kwargs):
        target = hermes_home
        try:
            if target is None:
                # No explicit home: the original would resolve the *scoped*
                # get_hermes_home(), which under /p/ is the user's profile.
                from hermes_constants import get_hermes_home

                resolved = get_hermes_home()
                if _is_user_profile_home(resolved):
                    target = envs.default_home()
            elif _is_user_profile_home(target):
                target = envs.default_home()
        except Exception:
            target = hermes_home  # never break logging over this
        return original(hermes_home=target, **kwargs)

    setattr(setup_logging, _PATCHED_ATTR, True)
    hermes_logging.setup_logging = setup_logging

    # agent_init imported the symbol directly at module load, so rebinding the
    # module attribute alone would miss the one call site that matters.
    for module_name in ("agent.agent_init",):
        try:
            import importlib
            import sys

            module = sys.modules.get(module_name) or importlib.import_module(module_name)
            if getattr(module, "setup_logging", None) is original:
                module.setup_logging = setup_logging
        except Exception:
            _log.debug("could not rebind setup_logging in %s", module_name, exc_info=True)

    _log.info("tcc-mcp-config: gateway logging pinned to the default profile")
