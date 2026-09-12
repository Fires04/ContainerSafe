"""Agent configuration: just SERVER_URL + AGENT_TOKEN (+ the DooD host
path, same gotcha as the server). Everything else — what to discover,
what to back up — is owned centrally by the server and pushed down over
the WebSocket connection on demand; this agent carries no job
configuration of its own, mirroring FiresLog's own agent design (see
docs/MULTI_HOST_PLAN.md). Re-pointing this agent at a different server is
a one-line .env change, not a redeploy.
"""

import os


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


SERVER_URL = _require("SERVER_URL")  # e.g. wss://containersafe.example.lan/ws/agent
AGENT_TOKEN = _require("AGENT_TOKEN")
AGENT_VERSION = os.environ.get("AGENT_VERSION", "0.1.0")

# Excluded from discovery/backup, same reasoning as the server's own
# APP_CONTAINER_NAME (see backend/app/config.py).
AGENT_CONTAINER_NAME = os.environ.get("AGENT_CONTAINER_NAME", "containersafe-agent")

# Host-absolute path that ./data resolves to on the machine THIS agent
# runs on — helper containers this agent spins up via the Docker API are
# started by the HOST daemon, which only understands host paths. Same
# convention as the server's own HOST_DATA_DIR.
HOST_DATA_DIR = _require("HOST_DATA_DIR")

DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
