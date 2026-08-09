"""Print each profile's resolved MCP wiring with keys fingerprinted, not shown."""

import hashlib
import sys
from pathlib import Path

import yaml

root = Path("/root/.hermes/profiles")
for entry in sorted(root.iterdir()):
    config_path = entry / "config.yaml"
    if not config_path.exists():
        continue
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        print(f"{entry.name}: unreadable config ({exc})")
        continue
    servers = config.get("mcp_servers") or {}
    if not servers:
        print(f"{entry.name}: no mcp_servers")
        continue
    for name, cfg in servers.items():
        auth = ((cfg or {}).get("headers") or {}).get("Authorization", "") or ""
        key = auth[len("Bearer "):] if auth.startswith("Bearer ") else auth
        digest = hashlib.sha256(key.encode()).hexdigest()[:12] if key else "EMPTY"
        print(
            "{:<24} server={:<16} url={:<40} keylen={:<5} sha={}".format(
                entry.name, name, str(cfg.get("url")), len(key), digest
            )
        )
