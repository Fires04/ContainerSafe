"""Docker introspection — an agent-side copy of backend/app/discovery.py's
inspect_one()/list_discovered(), trimmed to what the agent needs: it has
no DB of its own, so this just returns plain dicts to send up as a
`discovery.result` message. Deliberately duplicated rather than shared
via import from the server's own package (see docs/MULTI_HOST_PLAN.md:
this app's already-verified backend/app/ code stays untouched, this is
fresh code for the new agent). Keep this in sync with the server's
version if the inspect_one() shape ever changes.
"""

import logging

import docker
import docker.errors

log = logging.getLogger("containersafe_agentcore.discovery")

COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
COMPOSE_WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"
COMPOSE_CONFIG_FILES_LABEL = "com.docker.compose.project.config_files"


def _mount_summary(attrs: dict) -> list[dict]:
    mounts = []
    for m in attrs.get("Mounts", []):
        mounts.append(
            {
                "type": m.get("Type"),
                "name": m.get("Name"),
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
        network_id = net.get("NetworkID")
        entry = {
            "name": name,
            "network_id": network_id,
            "ip_address": net.get("IPAddress"),
            "aliases": net.get("Aliases"),
        }
        if not network_id:
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


def list_discovered(client: docker.DockerClient, exclude_name: str | None = None) -> list[dict]:
    """Inspect every container on this daemon except (optionally) the
    agent's own container."""
    results = []
    for container in client.containers.list(all=True):
        if exclude_name and container.name == exclude_name:
            continue
        try:
            results.append(inspect_one(client, container))
        except docker.errors.NotFound:
            continue
    return results
