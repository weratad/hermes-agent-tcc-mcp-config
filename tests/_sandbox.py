"""Pin plugin tests to a throwaway HERMES_HOME.

Never run these against a live gateway home — ``sync_default_profile_servers``
rewrites ``config.yaml``.
"""

from __future__ import annotations

import os
from pathlib import Path


_LIVE = {
    Path("/opt/data"),
    Path("/opt/tcc-hermes/data"),
    Path("/root/.hermes"),
}


def pin(envs, sandbox: Path) -> None:
    sandbox = sandbox.resolve()
    if sandbox in _LIVE or sandbox == (Path.home() / ".hermes").resolve():
        raise SystemExit(f"refusing to use live HERMES_HOME as sandbox: {sandbox}")
    os.environ["HERMES_HOME"] = str(sandbox)
    envs.default_home = lambda: sandbox
    try:
        import hermes_cli.profiles as hp

        hp._get_default_hermes_home = lambda: str(sandbox)
        hp._get_profiles_root = lambda: sandbox / "profiles"
        hp.get_profile_dir = lambda name: sandbox / "profiles" / name
    except Exception:
        pass
    home = Path(envs.default_home()).resolve()
    env_parent = Path(envs._default_env_path()).resolve().parent
    if home != sandbox or env_parent != sandbox:
        raise SystemExit(
            f"refusing to run tests: default_home={home} env={env_parent} sandbox={sandbox}"
        )
