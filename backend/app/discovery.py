"""Introspects containers on a DockerEndpoint: mounts, networks (full
entity data, not just names), compose labels, and the rest of what
backup.py/restore.py and the UI need. Writes/updates DiscoveredContainer
rows keyed by a stable identity (see models.py), never by the raw Docker
container ID.
"""

import logging
from datetime import datetime, timezone

import docker.errors
from sqlalchemy.orm import Session

from . import config
from .db import get_session
from .docker_endpoint import DockerEndpoint
from .models import DiscoveredContainer

log = logging.getLogger("containersafe.discovery")

COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
COMPOSE_WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"
COMPOSE_CONFIG_FILES_LABEL = "com.docker.compose.project.config_files"
COMPOSE_CONTAINER_NUMBER_LABEL = "com.docker.compose.container-number"


def _mount_summary(attrs: dict) -> list[dict]:
    mounts = []
    for m in attrs.get("Mounts", []):
        mounts.append(
            {
                "type": m.get("Type"),
                "name": m.get("Name"),  # only set for Type == "volume"
                "source": m.get("Source"),
                "destination": m.get("Destination"),
                "mode": m.get("Mode"),
                "rw": m.get("RW"),
                "propagation": m.get("Propagation"),
            }
        )
    return mounts


def _network_summary(client: docker.DockerClient, attrs: dict) -> list[dict]:
    networks = []
    for name, net in (attrs.get("NetworkSettings", {}).get("Networks") or {}).items():
        network_id = net.get("NetworkID")  # Docker API key is "NetworkID", not "NetworkId"
        entry = {
            "name": name,
            "network_id": network_id,
            "ip_address": net.get("IPAddress"),
            "aliases": net.get("Aliases"),
        }
        if not network_id:
            # Seen for e.g. "none"/"host" network mode entries, which have
            # no real network object behind them to inspect further.
            networks.append(entry)
            continue
        try:
            full = client.networks.get(network_id).attrs
            entry["driver"] = full.get("Driver")
            entry["ipam"] = full.get("IPAM")
            entry["internal"] = full.get("Internal")
            entry["attachable"] = full.get("Attachable")
            entry["labels"] = full.get("Labels")
        except docker.errors.NotFound:
            log.warning("Network %s (%s) vanished mid-inspect", name, network_id)
        networks.append(entry)
    return networks


def _identity_key(name: str, labels: dict) -> str:
    project = labels.get(COMPOSE_PROJECT_LABEL)
    service = labels.get(COMPOSE_SERVICE_LABEL)
    if project and service:
        # A scaled compose service (`docker compose up --scale x=N`) runs
        # several containers sharing the same project+service labels,
        # distinguished only by this container-number label — without it
        # in the key, replica 2+ would collide with replica 1 on the
        # (host_id, identity_key) unique constraint. Only suffix when
        # there's more than one replica so the common (unscaled) case
        # keeps today's identity_key unchanged — existing BackupJob rows
        # reference it and shouldn't need remapping.
        number = labels.get(COMPOSE_CONTAINER_NUMBER_LABEL)
        if number and number != "1":
            return f"{project}/{service}-{number}"
        return f"{project}/{service}"
    return name.lstrip("/")


def inspect_one(client: docker.DockerClient, container) -> dict:
    attrs = container.attrs
    config_ = attrs.get("Config", {}) or {}
    labels = config_.get("Labels") or {}
    host_config = attrs.get("HostConfig", {}) or {}
    name = attrs.get("Name", container.name)

    return {
        "docker_id": attrs.get("Id"),
        "identity_key": _identity_key(name, labels),
        "name": name.lstrip("/"),
        "image": config_.get("Image", ""),
        "status": attrs.get("State", {}).get("Status", "unknown"),
        "compose_project": labels.get(COMPOSE_PROJECT_LABEL),
        "compose_service": labels.get(COMPOSE_SERVICE_LABEL),
        "compose_working_dir": labels.get(COMPOSE_WORKING_DIR_LABEL),
        "compose_config_files": labels.get(COMPOSE_CONFIG_FILES_LABEL),
        "labels_json": labels,
        "mounts_json": _mount_summary(attrs),
        "networks_json": _network_summary(client, attrs),
        "env_json": config_.get("Env") or [],
        "ports_json": host_config.get("PortBindings") or {},
        "restart_policy_json": host_config.get("RestartPolicy") or {},
        "command_json": config_.get("Cmd"),
        "entrypoint_json": config_.get("Entrypoint"),
    }


def list_discovered(client: docker.DockerClient) -> list[dict]:
    """Inspect every container on this endpoint except this app's own."""
    results = []
    for container in client.containers.list(all=True):
        if container.name == config.APP_CONTAINER_NAME:
            continue
        try:
            results.append(inspect_one(client, container))
        except docker.errors.NotFound:
            # Removed between list() and inspect — just skip it, next
            # sync will simply not see it either.
            continue
    return results


def _upsert_rows(session: Session, host_id: str, seen: list[dict]) -> list[DiscoveredContainer]:
    """Upserts by (host_id, identity_key) so existing BackupJob rows
    (which reference identity_key) keep working across container
    recreation. Shared by sync() (this app's own local docker-py client)
    and sync_from_agent() (data reported by a remote agent over /ws/agent)
    — both produce the same inspect_one()-shaped dicts, just from a
    different source.
    """
    existing = {row.identity_key: row for row in session.query(DiscoveredContainer).filter_by(host_id=host_id).all()}

    now = datetime.now(timezone.utc)
    rows: list[DiscoveredContainer] = []
    for item in seen:
        row = existing.get(item["identity_key"])
        if row is None:
            row = DiscoveredContainer(host_id=host_id, identity_key=item["identity_key"])
            session.add(row)
            # Guard against two items in the same `seen` batch computing
            # the same identity_key (e.g. an _identity_key() edge case we
            # haven't accounted for) — without this, the second one would
            # try to INSERT a brand new row with the same (host_id,
            # identity_key) as the first and blow up the whole sync's
            # commit with a UNIQUE constraint violation instead of just
            # overwriting with the later (still valid) sighting.
            existing[item["identity_key"]] = row
        for field, value in item.items():
            setattr(row, field, value)
        row.last_seen_at = now
        rows.append(row)

    # Containers no longer present are kept in the DB (their BackupJob
    # history/config shouldn't vanish just because the container is
    # stopped-and-removed) but their status is not force-updated to
    # "unknown" here — `seen` only contains what's currently visible, so
    # anything fully removed simply stops appearing in it and keeps its
    # last known state.

    session.commit()
    return rows


def sync(session: Session, endpoint: DockerEndpoint) -> list[DiscoveredContainer]:
    """Refresh discovered_containers for a local DockerEndpoint (this
    app's own mounted socket)."""
    client = endpoint.client()
    seen = list_discovered(client)
    return _upsert_rows(session, endpoint.host_id, seen)


def sync_from_agent(host_id: str, containers: list[dict]) -> list[DiscoveredContainer]:
    """Refresh discovered_containers from a `discovery.result` message an
    agent sent over /ws/agent — same upsert as sync(), just fed by data
    the agent already inspected on its own host instead of a local
    docker-py client. Opens its own session (called via
    asyncio.to_thread from ws_agent.py's read loop, not already inside
    one)."""
    with get_session() as session:
        return _upsert_rows(session, host_id, containers)
