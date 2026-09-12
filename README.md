# ContainerSafe

Discovers the other Docker containers running on its host (volumes, bind
mounts, networks, compose labels, env, ports, restart policy), lets you
configure a backup job per container, and produces a single `.tar.gz`
archive per backup run containing both the container's data and a
`config.json` describing everything needed to recreate it — including
full network and volume entity definitions (driver, subnet, IPAM), not
just names. Archives can be restored into a new running container later.

Full design/architecture rationale (helper-container backup mechanism,
archive layout, restore flow, multi-host seam) lives in the implementation
plan this project was built from: see git history / the plan file this
was scaffolded against.

## Setup

```bash
cp .env.example .env
# edit .env: set APP_USERNAME/APP_PASSWORD/SESSION_SECRET, and HOST_DATA_DIR
# to the absolute host path of this repo's ./data directory (see the
# comment in .env.example for why this is required).
docker compose up --build
```

Then open `http://<host>:8093` and sign in.

## Deploying an agent (backing up a second host)

On the server's **Agents** page, click "New agent" and give it a name.
You get two one-time options (shown only once — copy what you need):

- **One-liner** (recommended): copy the `curl -fsSL .../agent-install/<code> | bash`
  command and run it directly on the second host. It downloads the
  agent's source, writes its `.env` (figuring out `HOST_DATA_DIR`
  automatically from wherever it actually ran), and starts it —
  nothing to fill in by hand. The link is single-use and expires in 15
  minutes; the bearer token only ever appears inside that one script
  response, never in your shell history.
- **Manual**: copy the `.env` snippet, get a copy of this repo (both
  `agent/` and `agentcore/`) onto the second host yourself, paste the
  snippet into `agent/.env`, fill in `HOST_DATA_DIR` (see that file's
  comment), then `cd agent && docker compose up -d --build`.

Either way, the agent should show **online** on the Agents page within a
few seconds, and its containers appear on the Containers page tagged
with its host name. Backup jobs can now target them the same way as
local ones.

## Security

Mounting `/var/run/docker.sock` gives this app effectively root-equivalent
access to the host — the Docker API can start a container with arbitrary
host mounts and capabilities, so there is no meaningful way to make that
socket "read-only" in practice. Because of this:

- Login is **always required** — there's no way to disable it, unlike
  some other apps in this family.
- The optional `/hostfs` read-only host-root mount (used only to fetch the
  real text of a discovered container's `docker-compose.yml`/`.env`,
  never required for actual backup/restore of data) is off by default and
  needs two separate opt-ins: `HOSTFS_ENABLED=true` in `.env` **and**
  uncommenting the mount line in `docker-compose.yml`.
- Treat this container's `.env` (`APP_PASSWORD`, `SESSION_SECRET`) and its
  access to `./backup` with the same care as root on the host.

## Status

Implemented and verified live against real containers on the host:
M0 (login/scaffold), M1 (read-only discovery), M2 (storage target +
backup job CRUD), M3 (backup execution — helper-container tar mechanism,
local storage, count-based retention), M4 (full `config.json` — network
and volume entities, normalized `recreate_spec`), M5 (restore — recreates
missing networks/volumes as real entities, restores data via the
mirror-image helper-container mechanism, creates+starts the container,
handles name collisions and missing images), M6 (remote storage over
rclone — SFTP/SMB/S3/WebDAV connection strings, password obscuring,
verified live against both a real SFTP server and a WebDAV server for
put/get/delete), M7 (APScheduler runs enabled jobs on their stored cron
automatically, resyncing immediately on every job create/update/delete;
"next run" shown in the UI), restore-from-uploaded-file (a small 2-step
wizard: upload a `.tar.gz` → review a preview, including a name-collision
warning — nothing touches Docker until you confirm), **multi-container
backup jobs** (one job can cover several containers — checkbox/multiselect
picker, one `BackupRun` per container per firing grouped under a shared
`batch_id`, retention scoped per-container so containers in the same job
don't compete for the same retention count), **env values hidden**
in the container list (only variable names are ever sent to the browser
— full values stay server-side, used only internally for backup/restore),
a **UI redesign** (Mantine, sidebar nav, dashboard with stat tiles +
recent activity), **cron help** (quick presets, a human-readable
description, and a live "next 3 runs" preview backed by
`GET /api/jobs/cron-preview`), **FTP/FTPS storage targets**, a
**Download** button next to Restore on successful runs
(`GET /api/runs/{run_id}/download`), and **live backup-run progress** —
`run-now` is asynchronous (returns a `batch_id` immediately via
`BackgroundTasks`) and each `BackupRun` reports a live
`current_stage` (`inspecting → archiving → packaging → uploading → done`)
plus in-stage progress, which `JobsPage` polls every 1.5s to show a real
progress bar, and **multi-host agents** (`docs/MULTI_HOST_PLAN.md`'s
M-Agent-1) — enroll one from the "Agents" page (one-time token + `.env`
snippet), deploy it as its own small Docker container next to the
containers it should back up (see `agent/` + its `agentcore/`
dependency, `agent/.env.example`). The agent authenticates over an
outbound WebSocket (bearer token, server-driven heartbeat), reports its
containers via discovery, and — when a job on its host runs — assembles
the backup archive locally and streams it back to the server in chunks;
the server alone ever talks to storage targets, so an agent never sees
target credentials. Cross-host restore, port-conflict handling during
restore, and agent-local storage targets are designed but not built yet
— see the doc's M-Agent-2 section. Not yet built: Alembic migrations
(hand-written idempotent migrations in `backend/app/migrations.py` cover
schema changes for now).
