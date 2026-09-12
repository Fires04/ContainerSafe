import os
from pathlib import Path


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# --- Login (FireAuth Pattern A) ---
# Unlike other apps in this family, login here is NOT REQUIRE_LOGIN-gated —
# mounting the Docker socket is effectively root-equivalent host access
# (see README's Security section), so it's always on.
APP_USERNAME = _require("APP_USERNAME")
APP_PASSWORD = _require("APP_PASSWORD")
SESSION_SECRET = _require("SESSION_SECRET")
COOKIE_HTTPS_ONLY = os.environ.get("COOKIE_HTTPS_ONLY", "false").lower() == "true"

# Optional: "Sign in with Authentik" alongside the password form. All five
# (including APP_EMAIL) must be set for it to turn on. Names are OIDC_*
# per PROJECT_STANDARDS.md's naming convention.
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "")
OIDC_REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "")

# SECURITY-CRITICAL (FireAuth's "Pattern A + OIDC binding" fix): without
# this, a successful Authentik login from *any* account on a shared
# instance would authenticate as this app's single operator.
APP_EMAIL = os.environ.get("APP_EMAIL", "")

OIDC_ENABLED = all([OIDC_CLIENT_ID, OIDC_CLIENT_SECRET, OIDC_ISSUER, OIDC_REDIRECT_URI, APP_EMAIL])

# --- Docker / host filesystem access ---
APP_CONTAINER_NAME = os.environ.get("APP_CONTAINER_NAME", "containersafe")

# Host-absolute path that ./data resolves to on the machine this container
# runs on. Required: helper containers this app spins up via the Docker
# API are started by the HOST daemon, which only understands host paths —
# see backend/app/docker_endpoint.py's host_path_for_data() and the
# .env.example comment for why.
HOST_DATA_DIR = _require("HOST_DATA_DIR")

# Off by default, two separate opt-ins required (this + docker-compose.yml's
# commented-out /hostfs mount) — see README's Security section. Only used
# for best-effort capture of a discovered container's real compose file
# text; never required for backing up/restoring actual container data.
HOSTFS_ENABLED = os.environ.get("HOSTFS_ENABLED", "false").lower() == "true"
HOSTFS_ROOT = Path("/hostfs")

# --- Paths inside this container ---
DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/app/backup"))
DB_PATH = DATA_DIR / "containersafe.db"
STAGING_DIR = DATA_DIR / "staging"
RESTORE_TMP_DIR = DATA_DIR / "restore_tmp"
INCOMING_DIR = DATA_DIR / "incoming"  # agent backup uploads land here mid-transfer, see backup.py

# --- Agents (server/agent multi-host model, see docs/MULTI_HOST_PLAN.md) ---
# Pepper for HMAC-SHA256-hashing agent enrollment tokens at rest — a bearer
# token is already 256 bits of random entropy, so this is about never having a
# plaintext/reversible copy sitting in the DB, not about slowing down
# guessing the way a password KDF would.
AGENT_TOKEN_PEPPER = _require("AGENT_TOKEN_PEPPER")
# Purely cosmetic — used only to pre-fill the SERVER_URL line of the
# .env snippet shown once at enrollment (routers/agents.py). This app has
# no reliable way to know its own externally-reachable address (may sit
# behind a reverse proxy under a different hostname), so this is an
# optional operator-provided hint; left blank, the snippet just shows a
# placeholder to fill in by hand.
AGENT_SERVER_URL = os.environ.get("AGENT_SERVER_URL", "")
AGENT_HEARTBEAT_INTERVAL_SECONDS = int(os.environ.get("AGENT_HEARTBEAT_INTERVAL_SECONDS", "30"))
AGENT_HEARTBEAT_TIMEOUT_SECONDS = int(os.environ.get("AGENT_HEARTBEAT_TIMEOUT_SECONDS", "90"))
AGENT_REQUEST_TIMEOUT_SECONDS = float(os.environ.get("AGENT_REQUEST_TIMEOUT_SECONDS", "10"))
