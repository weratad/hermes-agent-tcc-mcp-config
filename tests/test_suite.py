"""pytest wrapper around the standalone scripts in this directory.

The scripts take the plugin directory as argv[1] and are the source of truth.
This file exists so ``python -m pytest tests/`` works as documented.

``test_patch.py`` and ``test_startup.py`` need Hermes on PYTHONPATH; they are
skipped outside the gateway container.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent

STANDALONE = ("test_plugin.py", "test_live.py", "test_principal.py")
HERMES = ("test_patch.py", "test_startup.py")


def _has_hermes() -> bool:
    try:
        import gateway.platforms.api_server  # noqa: F401

        return True
    except Exception:
        return False


def _run(script: str) -> None:
    result = subprocess.run(
        [sys.executable, str(TESTS / script), str(PLUGIN)],
        cwd=str(PLUGIN),
    )
    assert result.returncode == 0, f"{script} failed with {result.returncode}"


@pytest.mark.parametrize("script", STANDALONE)
def test_standalone(script: str) -> None:
    _run(script)


@pytest.mark.skipif(not _has_hermes(), reason="needs Hermes gateway modules")
@pytest.mark.parametrize("script", HERMES)
def test_in_hermes(script: str) -> None:
    _run(script)
