# Multi-host / agent-server architecture

This is the design for extending ContainerSafe from "one host, one
process" to "one central server backing up containers across several
hosts."

## Status

**M-Agent-1 is built and live-verified**: agent enrollment (token +
`.env` snippet from the Agents page), the `/ws/agent` WebSocket protocol
(auth, heartbeat, online/offline tracking), discovery reported by an
agent, and real backup execution through an agent (archive assembled on
the agent's own host, streamed to the server in chunks, stored by the
server on an existing target — see `backend/app/agent_backup.py`,
`backend/app/agents/`, `backend/app/routers/ws_agent.py`,
`backend/app/routers/agents.py`, and the `agentcore/`/`agent/` packages).
Verified end-to-end with a real agent container, including surviving a
server restart and immediate offline detection on disconnect.

Also built: a one-command install flow mirroring the single-use
install-link pattern (`backend/app/agents/install_links.py` + public
`backend/app/routers/agent_install.py`) — enrolling an agent returns a
single-use, 15-minute `curl -fsSL .../agent-install/<code> | bash`
command that downloads the agent (a `tar.gz` of `agent/`+`agentcore/`
baked into the server's own image at build time — see the Dockerfile's
`agent-bundle` stage), writes its `.env` (computing `HOST_DATA_DIR` from
where it actually ran, not left to the operator), and starts it. The
manual `.env`-snippet path from M-Agent-1 still works as a fallback.

**M-Agent-2 is not built yet** — see its own section below: cross-host
restore, port-conflict detection/remap during restore, restore health
verification, and agent-local storage targets (the `StorageTarget.host_id`
column exists for this already, but nothing reads it yet).

The rest of this document is the design that M-Agent-1 was built from —
kept as-is below since it's still the accurate reference for both what
exists and what M-Agent-2 builds next.

## Why this shape

The MVP already carries the seam for this: every endpoint-scoped table has
a `host_id` column (always `"local"` today), and all Docker access goes
through the `DockerEndpoint` abstract base class rather than a bare
`docker.DockerClient`. The one thing the MVP-era version of that seam got
wrong is worth calling out explicitly, because it changes the design below:
**`DockerEndpoint` alone is not sufficient.** `backup.py`'s helper-container
mechanism (`archive.py`) stages data through a directory that a *host's own
Docker daemon* resolves as a bind-mount source — that staging path is
inherently host-local. Making `DockerEndpoint.client()` return a client
pointed at a remote daemon would not make the staging directory remote too;
the helper container would still be told to mount a path from the
*server's* filesystem, which the remote daemon can't see. So the real
split isn't "swap the Docker client," it's "run the assembly step where the
container actually lives."

## Components

- **Server** — this app, unchanged in its core: the DB, the web UI, the
  scheduler, and every `StorageTarget`'s credentials. Owns the single
  source of truth for jobs, targets, and run history, same as today.
- **Agent** — a new, minimal sibling artifact. Architecturally it's *this
  same app with the DB/UI/scheduler/targets stripped out*, pointed at its
  own local `/var/run/docker.sock`: it reuses `discovery.py`, `archive.py`,
  and the assembly half of `backup.py`/`restore.py` as-is. Its only new
  code is the connection to the server and a thin RPC dispatcher.

## Design precedent: a push-agent model

The problem "how does a central server reach Docker data on a host it
doesn't have inbound access to" has a well-known shape: a lightweight
agent on each monitored host opens an **outbound** WebSocket to the
central server and authenticates with a per-agent token generated at
enrollment (`install.sh`-style flow, agent's own `.env` holds
`SERVER_URL` + `AGENT_TOKEN`). No inbound port, no SSH, no VPN. This plan
adopts that pattern rather than inventing a new one — a well-understood
trust model and operational shape.

## Data model addition

```
agents(id, host_id, display_name, token_hash, enrolled_at, last_seen_at, status)
```

`host_id` here is what `DiscoveredContainer.host_id`, `BackupJob.host_id`
etc. already reference — enrolling an agent is what makes a new `host_id`
value meaningful and selectable in the UI. Existing `"local"` rows are
untouched; this table is purely additive.

## `RemoteAgentEndpoint(DockerEndpoint)`

The server keeps an in-memory registry of live agent WebSocket connections
keyed by `host_id`, populated as agents connect/disconnect (backed by the
`agents` table for enrollment/auth, not for the live-connection state
itself). `RemoteAgentEndpoint.client()` doesn't return a `docker.DockerClient`
at all — see below, the split is at a higher level than the Docker client.

## The actual split: assemble locally, store centrally

- **`assemble_archive(container)` → local `.tar.gz` + `config.json`** —
  everything through today's `backup.py` body up to "the archive file
  exists on disk." This step is inherently host-local (see "Why this
  shape" above) and runs:
  - unchanged, in-process, for `host_id == "local"` (today's behavior,
    untouched);
  - **on the agent**, for any other `host_id` — the agent has its own
    local `archive.py`/`discovery.py`, its own local staging directory,
    its own local Docker socket, so the entire mechanism that already
    works today works identically there, just relocated.
- **`store_and_record(archive, job, run)` → StorageTarget + DB** —
  everything after "the archive file exists": `build_backend(target).put()`,
  the `BackupRun` row, retention. This step **always runs on the server**,
  regardless of which host the container was on.

For a remote host, the agent streams the finished archive back to the
server in chunks over the same authenticated WS connection once assembly
finishes; the server writes it to a temp file and calls the exact same
`store_and_record()` it already uses for local jobs today. Restore is the
mirror image: the server sends the agent an archive to unpack, the agent
runs the existing `restore.py` mechanics locally against its own Docker
daemon, and reports success/failure back.

## Security choice: agents never hold storage credentials

`StorageTarget` credentials (SFTP/SMB passwords, S3 keys) stay
server-side only — an agent is never handed them, even for a job backing
up its own host's containers. This is a deliberate tradeoff: it means
every archive makes two hops (agent → server → target) instead of one for
remote hosts, but it keeps each agent's blast radius limited to exactly
its own host's Docker socket plus one outbound authenticated channel — an
agent compromise never leaks credentials for storage the operator uses
elsewhere. A direct agent-to-storage variant is possible later if
double-hop bandwidth ever becomes a real bottleneck, but isn't the
starting design.

## Protocol

A small, narrow, typed message set over the WS connection — **not** a
generic tunnel onto the Docker Engine API (that would hand a compromised
or buggy agent far more surface than it needs):

```
backup.request   { job_id, identity_key, run_id }
backup.chunk     { run_id, seq, data }        # archive bytes, chunked
backup.complete  { run_id, size_bytes }
backup.error     { run_id, message }

restore.request  { restore_id, archive (chunked same way) }
restore.progress { restore_id, message }
restore.complete { restore_id, target_identity_key }
restore.error    { restore_id, message }
```

## Deployment

The agent ships as its own small Docker image/compose file — a sibling
artifact at `ContainerSafe/agent/`, mounting only its own
`/var/run/docker.sock` (same root-equivalent-access caveat as the server
itself, documented the same way). Enrollment: generate a token on the
server (shown once), drop it into the agent's `.env` as `AGENT_TOKEN`
alongside `SERVER_URL`, `docker compose up -d` on the remote host.

## Migration path

Purely additive. Every existing `"local"` job/target keeps working
unchanged. A remote host's containers only become selectable in the
"Containers" list — and therefore assignable to a job — once its agent
has enrolled, connected, and reported a discovery pass, exactly mirroring
how `"local"` containers already need at least one discovery sync before
they're job-able.

## M-Agent-2 (next, not built yet)

- **Cross-host restore**: restore an archive backed up from one agent's
  host as a new container on a *different* host (or the same one).
  Mirrors the backup direction: `restore.request` (server -> agent,
  chunked archive going *down* this time via `restore.chunk`), the target
  agent runs the same recreate-network/volume/data/container logic
  `restore.py` already has (ported into `agentcore/`), reporting
  `restore.stage`/`restore.complete`/`restore.error` back.
- **Port-conflict detection + remap during restore**: before starting the
  restored container, check its `recreate_spec.ports` against every
  container Docker already knows about on the target host (catches the
  common case; a non-Docker process already holding the port is outside
  what the Docker API alone can see) — a new `restore.port_conflict`
  message pauses for the operator to remap, resumed via
  `restore.port_remap`.
- **Restore health verification**: after start, confirm the container is
  actually running (and stays running past its restart-policy window) —
  or if it declares a Docker `HEALTHCHECK`, wait for that instead — and
  report the result rather than treating "container object created" as
  success.
- **Agent-local storage targets**: let a target write directly to an
  agent's own disk (`StorageTarget.host_id` already supports naming this,
  nothing reads it yet) without the archive ever passing through the
  server — the server would need a new on-demand `archive.fetch` request
  to pull a copy back from that agent whenever a download/restore needs
  one.
