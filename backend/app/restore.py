"""Restores a container from an archive — either one of our own recorded
BackupRun rows, or a .tar.gz a user hands us directly (upload wizard:
stage_upload() extracts + previews without touching Docker, then
restore_from_upload() does the actual work once confirmed). Both paths
share _perform_restore(): recreate missing networks/volumes as real
entities, restore data into them via the mirror-image of backup.py's
helper-container mechanism, pull the image if needed, then create+start
the container from config.json's recreate_spec.

Like backup.py, both restore_backup_run() and restore_from_upload()
always return a RestoreRun id once the run row exists — failures after
that point are recorded ON the run rather than raised. RestoreError is
only raised for problems discovered before a run row can be created (or,
for stage_upload/discard_upload, which never create one at all).
"""

import json
import logging
import shutil
import tarfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import docker.errors
import docker.types

from . import config
from .archive import ArchiveError, untar_staging_to_target
from .db import get_session
from .docker_endpoint import get_endpoint
from .models import BackupJob, BackupRun, RestoreRun, StorageTarget
from .storage import StorageError, build_backend

log = logging.getLogger("containersafe.restore")

BUILTIN_NETWORK_NAMES = {"bridge", "host", "none"}
UPLOAD_MAX_AGE_SECONDS = 3600  # abandoned (never confirmed/discarded) uploads self-clean after this


class RestoreError(Exception):
    """Raised only for failures before a RestoreRun row exists yet (or
    for stage_upload/discard_upload, which never create one)."""


def _convert_ports(ports_json: dict) -> dict | None:
    """host_config['PortBindings'] shape -> containers.run()'s `ports` kwarg
    shape. {"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8093"}]} ->
    {"8000/tcp": [("0.0.0.0", "8093")]} (docker-py accepts a list of
    (ip, port) tuples for multiple host bindings on one container port)."""
    if not ports_json:
        return None
    result = {}
    for container_port, bindings in ports_json.items():
        if not bindings:
            result[container_port] = None
            continue
        result[container_port] = [(b.get("HostIp") or "", b.get("HostPort")) for b in bindings]
    return result


def _convert_volumes(mounts: list[dict]) -> dict:
    """recreate_spec['mounts'] -> containers.run()'s `volumes` kwarg. For
    type=="volume" the key is the volume NAME (no leading "/"), which is
    exactly how docker-py/the Docker API tells a named volume apart from
    a bind path — this is the same -v NAME:/path vs -v /host:/path
    disambiguation `docker run` itself relies on."""
    volumes = {}
    for m in mounts:
        key = m["name"] if m["type"] == "volume" and m["name"] else m["source"]
        if not key:
            continue
        volumes[key] = {"bind": m["target"], "mode": "ro" if m["read_only"] else "rw"}
    return volumes


def _ensure_network(client: docker.DockerClient, net: dict, log_line) -> None:
    name = net.get("name")
    if not name or name in BUILTIN_NETWORK_NAMES:
        return
    existing = client.networks.list(names=[name])
    if existing:
        real = existing[0].attrs
        if net.get("driver") and real.get("Driver") != net.get("driver"):
            log_line(
                f"Network {name!r} already exists with driver {real.get('Driver')!r}, "
                f"not {net.get('driver')!r} — using the existing one as-is"
            )
        return

    ipam = net.get("ipam") or {}
    pool_configs = [
        docker.types.IPAMPool(
            subnet=c.get("Subnet"),
            gateway=c.get("Gateway"),
            iprange=c.get("IPRange"),
            aux_addresses=c.get("AuxiliaryAddresses"),
        )
        for c in (ipam.get("Config") or [])
    ]
    ipam_config = docker.types.IPAMConfig(driver=ipam.get("Driver") or "default", pool_configs=pool_configs)
    log_line(f"Creating network {name!r} (driver={net.get('driver')!r})")
    client.networks.create(
        name=name,
        driver=net.get("driver") or "bridge",
        options=net.get("options") or None,
        ipam=ipam_config,
        internal=bool(net.get("internal")),
        attachable=bool(net.get("attachable")),
        labels=net.get("labels") or {},
    )


def _ensure_volume(client: docker.DockerClient, vol: dict, log_line) -> None:
    name = vol.get("name")
    if not name:
        return
    try:
        client.volumes.get(name)
        return
    except docker.errors.NotFound:
        pass
    log_line(f"Creating volume {name!r} (driver={vol.get('driver')!r})")
    client.volumes.create(
        name=name,
        driver=vol.get("driver") or "local",
        driver_opts=vol.get("options") or {},
        labels=vol.get("labels") or {},
    )


def _find_by_name(client: docker.DockerClient, name: str):
    return next((c for c in client.containers.list(all=True) if c.name == name), None)


def _perform_restore(
    client: docker.DockerClient,
    extract_dir_host: str,
    config_json: dict,
    container_name_override: str | None,
    allow_takeover: bool,
    log_line,
) -> str:
    """The actual restore mechanics, shared by both entry points below.
    Returns the final container name. Raises RestoreError/ArchiveError/
    docker exceptions on failure — callers wrap this in their own
    run-tracking try/except."""
    recreate_spec = config_json["recreate_spec"]

    for net in config_json.get("networks", []):
        _ensure_network(client, net, log_line)

    for vol in config_json.get("volumes", []):
        _ensure_volume(client, vol, log_line)

    for item in config_json.get("data_manifest", []):
        if item["kind"] == "volumes":
            target_host_path = client.volumes.get(item["name"]).attrs["Mountpoint"]
        else:
            target_host_path = item["name"]
        member = Path(item["archive_member"])
        log_line(f"Restoring {item['kind']} {item['name']} -> {target_host_path}")
        untar_staging_to_target(
            client,
            archive_dir_host=f"{extract_dir_host}/{member.parent.as_posix()}",
            archive_filename=member.name,
            target_host_path=target_host_path,
        )

    image = recreate_spec["image"]
    try:
        client.images.get(image)
    except docker.errors.ImageNotFound:
        log_line(f"Pulling image {image!r}")
        client.images.pull(image)

    base_name = container_name_override or config_json.get("container_name") or config_json["identity_key"]
    existing = _find_by_name(client, base_name)
    final_name = base_name
    if existing:
        if allow_takeover:
            if existing.status == "running":
                raise RestoreError(f"Container {base_name!r} is running — stop it before taking over its name")
            log_line(f"Removing existing stopped container {base_name!r} to take over its name")
            existing.remove(force=True)
        else:
            final_name = f"{base_name}-restored-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
            log_line(f"Name {base_name!r} already in use — restoring as {final_name!r} instead")

    restart_policy = recreate_spec.get("restart_policy") or None
    if restart_policy and not restart_policy.get("Name"):
        restart_policy = None

    networks = [n for n in recreate_spec.get("networks", []) if n not in BUILTIN_NETWORK_NAMES]
    first_network = networks[0] if networks else None

    log_line(f"Creating container {final_name!r} from image {image!r}")
    container = client.containers.run(
        image=image,
        name=final_name,
        command=recreate_spec.get("command"),
        entrypoint=recreate_spec.get("entrypoint"),
        environment=recreate_spec.get("environment") or [],
        labels=recreate_spec.get("labels") or {},
        ports=_convert_ports(recreate_spec.get("ports") or {}),
        restart_policy=restart_policy,
        volumes=_convert_volumes(recreate_spec.get("mounts") or []),
        network=first_network,
        detach=True,
    )
    for net_name in networks[1:]:
        try:
            client.networks.get(net_name).connect(container)
        except docker.errors.NotFound:
            log_line(f"Network {net_name!r} not found when attaching second interface, skipped")

    log_line(f"Container {final_name!r} started ({container.short_id})")
    return final_name


_RESTORE_EXCEPTIONS = (
    ArchiveError,
    StorageError,
    RestoreError,
    docker.errors.DockerException,
    OSError,
    KeyError,
    json.JSONDecodeError,
)


def restore_backup_run(run_id: int, container_name_override: str | None = None, allow_takeover: bool = False) -> int:
    with get_session() as session:
        backup_run = session.get(BackupRun, run_id)
        if backup_run is None or backup_run.status != "success":
            raise RestoreError(f"Backup run {run_id} not found or wasn't successful")
        job = session.get(BackupJob, backup_run.job_id)
        if job is None:
            raise RestoreError("This run's backup job no longer exists")
        target = session.get(StorageTarget, job.storage_target_id)
        if target is None:
            raise RestoreError("This job's storage target no longer exists")
        if job.host_id != "local":
            # Restoring back onto (or across to a different) agent isn't
            # built yet — see docs/MULTI_HOST_PLAN.md's M-Agent-2. Raised
            # before any RestoreRun row exists, same as the other
            # pre-flight checks above, rather than surfacing as a raw 500
            # from get_endpoint() rejecting an unknown host_id.
            raise RestoreError(
                f"This backup was made via agent {job.host_id!r} — restoring an agent-hosted backup "
                "isn't supported yet. You can still download the archive from this run."
            )

        restore_run = RestoreRun(source_archive_locator=backup_run.archive_locator, status="running")
        session.add(restore_run)
        session.commit()
        session.refresh(restore_run)
        restore_id = restore_run.id
        job_host_id = job.host_id
        archive_locator = backup_run.archive_locator

    log_lines: list[str] = []

    def log_line(msg: str) -> None:
        log_lines.append(msg)
        log.info("[restore %s] %s", restore_id, msg)

    def fail(error: Exception) -> int:
        log.exception("Restore run %s failed", restore_id)
        with get_session() as session:
            run = session.get(RestoreRun, restore_id)
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            run.error_message = str(error)
            run.log_text = "\n".join(log_lines)
            session.commit()
        return restore_id

    work_dir: Path | None = None
    try:
        if not archive_locator:
            return fail(RestoreError("This backup run has no archive on record"))

        endpoint = get_endpoint(job_host_id)
        client = endpoint.client()
        backend = build_backend(target)

        work_dir = config.RESTORE_TMP_DIR / str(restore_id)
        work_dir.mkdir(parents=True, exist_ok=True)
        archive_local = work_dir / "archive.tar.gz"
        extract_dir = work_dir / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)

        log_line(f"Fetching archive {archive_locator!r} from target {target.name!r}")
        backend.get(archive_locator, archive_local)
        with tarfile.open(archive_local, "r:gz") as tf:
            tf.extractall(extract_dir, filter="data")

        config_json = json.loads((extract_dir / "config.json").read_text())
        extract_dir_host = endpoint.host_path_for_data(f"restore_tmp/{restore_id}/extracted")

        final_name = _perform_restore(
            client, extract_dir_host, config_json, container_name_override, allow_takeover, log_line
        )

        with get_session() as session:
            run = session.get(RestoreRun, restore_id)
            run.status = "success"
            run.finished_at = datetime.now(timezone.utc)
            run.target_identity_key = final_name
            run.log_text = "\n".join(log_lines)
            session.commit()
        return restore_id

    except _RESTORE_EXCEPTIONS as exc:
        return fail(exc)
    finally:
        if work_dir is not None:
            shutil.rmtree(work_dir, ignore_errors=True)


# --- Upload wizard: stage (extract + preview, no Docker changes) then confirm ---


def _upload_dir(upload_id: str) -> Path:
    return config.RESTORE_TMP_DIR / f"upload-{upload_id}"


def _cleanup_stale_uploads() -> None:
    now = time.time()
    if not config.RESTORE_TMP_DIR.is_dir():
        return
    for entry in config.RESTORE_TMP_DIR.iterdir():
        if entry.name.startswith("upload-") and entry.is_dir():
            try:
                if now - entry.stat().st_mtime > UPLOAD_MAX_AGE_SECONDS:
                    shutil.rmtree(entry, ignore_errors=True)
            except OSError:
                pass


def _build_preview(config_json: dict, host_id: str = "local") -> dict:
    recreate_spec = config_json.get("recreate_spec", {})
    base_name = config_json.get("container_name") or config_json.get("identity_key")

    name_conflict = None
    name_conflict_running = None
    try:
        client = get_endpoint(host_id).client()
        existing = _find_by_name(client, base_name)
        name_conflict = existing is not None
        name_conflict_running = bool(existing and existing.status == "running")
    except docker.errors.DockerException:
        pass  # daemon unreachable — preview still useful without this hint

    return {
        "identity_key": config_json.get("identity_key"),
        "container_name": base_name,
        "image": recreate_spec.get("image"),
        "created_at": config_json.get("created_at"),
        "app_version": config_json.get("app_version"),
        "compose": config_json.get("compose"),
        "schema_version": config_json.get("schema_version"),
        "networks": [{"name": n.get("name"), "driver": n.get("driver")} for n in config_json.get("networks", [])],
        "volumes": [{"name": v.get("name"), "driver": v.get("driver")} for v in config_json.get("volumes", [])],
        "mounts": recreate_spec.get("mounts", []),
        "data_manifest": [
            {"kind": m["kind"], "name": m["name"], "destination": m["destination"]}
            for m in config_json.get("data_manifest", [])
        ],
        "ports": recreate_spec.get("ports"),
        "restart_policy": recreate_spec.get("restart_policy"),
        "name_conflict": name_conflict,
        "name_conflict_running": name_conflict_running,
    }


def stage_upload(file_path: Path, host_id: str = "local") -> dict:
    """Extracts an uploaded archive into a fresh staging dir and returns
    {upload_id, preview} — pure inspection, touches Docker only to check
    for a container-name clash (best-effort, never fatal to the preview).
    Raises RestoreError if the file isn't a ContainerSafe archive.
    """
    _cleanup_stale_uploads()
    upload_id = uuid.uuid4().hex
    extract_dir = _upload_dir(upload_id)
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(file_path, "r:gz") as tf:
            tf.extractall(extract_dir, filter="data")
    except tarfile.TarError as exc:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise RestoreError(f"Not a valid .tar.gz archive: {exc}") from None

    config_path = extract_dir / "config.json"
    if not config_path.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise RestoreError("Archive has no config.json — doesn't look like a ContainerSafe backup")

    try:
        config_json = json.loads(config_path.read_text())
    except json.JSONDecodeError as exc:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise RestoreError(f"config.json is not valid JSON: {exc}") from None

    return {"upload_id": upload_id, "preview": _build_preview(config_json, host_id)}


def discard_upload(upload_id: str) -> None:
    shutil.rmtree(_upload_dir(upload_id), ignore_errors=True)


def restore_from_upload(
    upload_id: str, container_name_override: str | None = None, allow_takeover: bool = False, host_id: str = "local"
) -> int:
    extract_dir = _upload_dir(upload_id)
    config_path = extract_dir / "config.json"
    if not config_path.exists():
        raise RestoreError(f"Upload {upload_id!r} not found — it may have expired or already been used")

    with get_session() as session:
        restore_run = RestoreRun(source_archive_locator=f"upload:{upload_id}", status="running")
        session.add(restore_run)
        session.commit()
        session.refresh(restore_run)
        restore_id = restore_run.id

    log_lines: list[str] = []

    def log_line(msg: str) -> None:
        log_lines.append(msg)
        log.info("[restore %s] %s", restore_id, msg)

    def fail(error: Exception) -> int:
        log.exception("Restore run %s (from upload) failed", restore_id)
        with get_session() as session:
            run = session.get(RestoreRun, restore_id)
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            run.error_message = str(error)
            run.log_text = "\n".join(log_lines)
            session.commit()
        return restore_id

    try:
        endpoint = get_endpoint(host_id)
        client = endpoint.client()
        config_json = json.loads(config_path.read_text())
        extract_dir_host = endpoint.host_path_for_data(f"restore_tmp/upload-{upload_id}")

        final_name = _perform_restore(
            client, extract_dir_host, config_json, container_name_override, allow_takeover, log_line
        )

        with get_session() as session:
            run = session.get(RestoreRun, restore_id)
            run.status = "success"
            run.finished_at = datetime.now(timezone.utc)
            run.target_identity_key = final_name
            run.log_text = "\n".join(log_lines)
            session.commit()
        return restore_id

    except _RESTORE_EXCEPTIONS as exc:
        return fail(exc)
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
