"""TCC MCP Config — one gateway, one MCP, one profile per user.

Three responsibilities, deliberately in ONE plugin so they cannot drift apart:

1. ``environments`` — the MCP URL / MCP key / gateway key. Edited from the
   dashboard tab (``dashboard/``), materialized into every profile.
2. ``provisioner`` — creates ``staff-<id>`` / ``user-<id>[-store-<id>]`` the
   first time that user chats, so each user gets their own ``memories/MEMORY.md``.
3. ``principal_injector`` — stamps the authenticated session key onto every TCC
   MCP call. Without it every call is fail-closed and answers come back empty.

Keeping them together matters: the tool prefix the injector matches is derived
from the same ``MCP_SERVER_NAME`` the config writer and the profile template
use, so renaming the MCP server cannot silently disarm injection — which is
exactly what happened when the name and the injector lived in separate plugins.

Plugins are discovered ONCE per process, under the default profile
(``hermes_cli.plugins`` keeps a module-level manager with a ``_discovered``
guard), and the registrations are process-wide. That is why per-user profiles
need no copy of this plugin: the injector and the patches already cover every
``/p/<profile>/`` request.
"""

from __future__ import annotations

import logging

from . import environments, log_scope, principal_injector, provisioner

__all__ = ["environments", "log_scope", "principal_injector", "provisioner", "register"]

_log = logging.getLogger("hermes.plugin.tcc-mcp-config")


def register(ctx) -> None:
    principal_injector.register(ctx)
    provisioner.install()
    log_scope.install()

    # Must happen HERE, at plugin-load time: the gateway loads plugins and then
    # runs MCP discovery once, under the default profile. Writing the servers
    # any later means they are not connected until the next restart, and every
    # user's turn comes back with no tools at all.
    try:
        if environments.sync_default_profile_servers():
            _log.info("tcc-mcp-config: refreshed MCP servers on the default profile")
        # Record what THIS process will connect with, so the dashboard can tell
        # "saved" from "actually running" and stop claiming MCP is ready when
        # it is only ready after the next restart.
        environments.mark_live()
    except Exception:
        _log.exception(
            "tcc-mcp-config: could not declare the MCP server — "
            "chat turns may find no tools until this is fixed"
        )

    _log.info("tcc-mcp-config ready (mcp server: %s)", environments.MCP_SERVER_NAME)
