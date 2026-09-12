"""Builds one container's backup archive (data + config.json) — agent-side
equivalent of the assembly half of backend/app/backup.py's
_backup_one_container(), stopping once the archive file exists on disk.
The agent's own dispatch.py streams the result to the server from there;
the server-side twin (agent_backup.py) owns storage/retention. See
docs/MULTI_HOST_PLAN.md.
"""

import json
import tarfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import docker
import docker.errors

from .archive import sanitize, tar_source_to_staging
from .discovery import inspect_one

StageCallback = Callable[[str, int | None, int | None], None]
LogCallback = Callable[[str], None]


def _build_recreate_spec(attrs: dict) -> dict:
    return {
        "image": attrs["image"],
        "command": attrs["command_json"],
        "entrypoint": attrs["entrypoint_json"],
        "environment": attrs["env_json"],
        "labels": attrs["labels_json"],
        "restart_policy": attrs["restart_policy_json"],
        "ports": attrs["ports_json"],
        "mounts": [
            {
                "type": m["type"],
                "name": m["name"],
                "source": m["source"],
                "target": m["destination"],
                "read_only": not m["rw"],
            }
            for m in attrs["mounts_json"]
        ],
        "networks": [n["name"] for n in attrs["networks_json"]],
    }


def assemble_backup_archive(
    client: docker.DockerClient,
    docker_id: str,
    identity_key: str,
    host_id: str,
    include_bind_mounts: bool,
    staging_local: Path,
    staging_host: str,
    agent_version: str,
    on_stage: StageCallback,
    log_line: LogCallback,
) -> Path:
    """Returns the local path to the finished .tar.gz. Raises
    ArchiveError / docker exceptions on failure — the caller
    (dispatch.py) turns those into a backup.error message."""
    container = client.containers.get(docker_id)
    attrs = inspect_one(client, container)

    archivable = [
        m
        for m in attrs["mounts_json"]
        if m["type"] in ("volume", "bind") and m["source"] and (m["type"] != "bind" or include_bind_mounts)
    ]
    on_stage("archiving", 0, len(archivable))

    data_manifest = []
    archived_count = 0
    for mount in attrs["mounts_json"]:
        if mount["type"] not in ("volume", "bind"):
            log_line(f"Skipping {mount['type']} mount {mount['destination']} (no persistent data)")
            continue
        if mount["type"] == "bind" and not include_bind_mounts:
            log_line(f"Skipping bind mount {mount['source']} (bind mounts disabled for this job)")
            continue
        if not mount["source"]:
            log_line(f"Skipping mount {mount['destination']} (no host source path reported)")
            continue

        kind = "volumes" if mount["type"] == "volume" else "bind"
        raw_name = mount["name"] or mount["source"]
        member_name = f"{kind}__{sanitize(raw_name)}"
        log_line(f"Archiving {mount['type']} mount {raw_name} -> {mount['destination']}")
        tar_source_to_staging(client, mount["source"], staging_host, member_name, staging_local)
        data_manifest.append(
            {
                "kind": kind,
                "name": raw_name,
                "destination": mount["destination"],
                "archive_member": f"data/{kind}/{sanitize(raw_name)}.tar.gz",
            }
        )
        archived_count += 1
        on_stage("archiving", archived_count, len(archivable))

    volume_entities = []
    for mount in attrs["mounts_json"]:
        if mount["type"] != "volume" or not mount["name"]:
            continue
        try:
            vol_attrs = client.volumes.get(mount["name"]).attrs
            volume_entities.append(
                {
                    "name": vol_attrs.get("Name"),
                    "driver": vol_attrs.get("Driver"),
                    "options": vol_attrs.get("Options"),
                    "labels": vol_attrs.get("Labels"),
                }
            )
        except docker.errors.NotFound:
            log_line(f"Volume {mount['name']} vanished mid-backup, skipping its entity data")

    on_stage("packaging", None, None)
    config_json = {
        "schema_version": 1,
        "app_version": agent_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_host_id": host_id,
        "identity_key": identity_key,
        "docker_id": attrs["docker_id"],
        "container_name": attrs["name"],
        "compose": {
            "project": attrs["compose_project"],
            "service": attrs["compose_service"],
            "working_dir": attrs["compose_working_dir"],
            "config_files": attrs["compose_config_files"],
            "file_available": False,
        },
        "container_inspect": container.attrs,
        "networks": attrs["networks_json"],
        "volumes": volume_entities,
        "recreate_spec": _build_recreate_spec(attrs),
        "data_manifest": data_manifest,
    }
    config_path = staging_local / "config.json"
    config_path.write_text(json.dumps(config_json, indent=2, default=str))

    archive_name = f"{sanitize(identity_key)}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.tar.gz"
    final_path = staging_local / archive_name
    with tarfile.open(final_path, "w:gz") as tf:
        tf.add(config_path, arcname="config.json")
        for item in data_manifest:
            member_path = staging_local / f"{item['kind']}__{sanitize(item['name'])}.tar.gz"
            tf.add(member_path, arcname=item["archive_member"])

    return final_path
