"""Orchestrates backup runs: fetch live container state, tar each mount
via a helper container (archive.py), assemble the outer .tar.gz +
config.json, hand it to the job's StorageBackend, then apply retention.

A job can list several containers (BackupJob.identity_keys_json) — one
firing (manual "Run now" or a scheduled fire) produces one BackupRun PER
container, all sharing a `batch_id` so the UI can group them, but each
independently restorable exactly like before multi-container jobs
existed. run_backup_job() returns that batch_id.

Each individual container's backup (_backup_one_container) always
returns a BackupRun id once its row exists — failures after that point
are recorded ON the run (status="failed", error_message) rather than
raised, so a caller always has a run to show the user, and one
container's failure doesn't stop the rest of the batch. BackupError is
only raised for problems discovered BEFORE any run row can be created
(job/target missing, or a job with no member containers at all).
"""

import json
import logging
import shutil
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import docker.errors

from . import config
from .agents.registry import AgentOfflineError, get_agent_registry
from .archive import ArchiveError, sanitize, tar_source_to_staging
from .db import get_session
from .discovery import inspect_one
from .docker_endpoint import get_endpoint
from .models import BackupJob, BackupRun, DiscoveredContainer, StorageTarget
from .storage import StorageError, build_backend

log = logging.getLogger("containersafe.backup")


class BackupError(Exception):
    """Raised only for failures before any BackupRun row exists yet."""


def _build_recreate_spec(attrs: dict) -> dict:
    """Normalized shape restore.py consumes directly, rather than
    re-deriving containers.run() kwargs from the raw inspect dump every
    time. See attrs' own keys (discovery.inspect_one)."""
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


def run_backup_job(job_id: int, batch_id: str | None = None) -> str:
    """Runs every member container of a job and returns the shared
    batch_id. Raises BackupError only if the job/target can't be
    resolved at all, or the job has no member containers — anything that
    fails per-container is recorded on that container's own BackupRun
    instead (see _backup_one_container), never raised here.

    `batch_id` can be supplied by the caller (routers/jobs.py's run-now
    generates one up front and hands it back to the HTTP client
    immediately, before this function — dispatched as a background task —
    has actually done anything, so the UI has something to poll
    "GET .../runs" for right away instead of only after the whole batch
    finishes)."""
    with get_session() as session:
        job = session.get(BackupJob, job_id)
        if job is None:
            raise BackupError(f"No such backup job: {job_id}")
        target = session.get(StorageTarget, job.storage_target_id)
        if target is None:
            raise BackupError("This job's storage target no longer exists")
        scope_type = job.scope_type
        compose_project = job.compose_project
        identity_keys = list(job.identity_keys_json or [])
        if scope_type == "project":
            if not compose_project:
                raise BackupError("This job has no compose project configured")
        elif not identity_keys:
            raise BackupError("This job has no member containers")
        job_host_id = job.host_id
        job_dest_subpath = job.dest_subpath
        job_include_bind_mounts = job.include_bind_mounts

    batch_id = batch_id or uuid.uuid4().hex

    if scope_type == "project":
        if job_host_id != "local":
            # Agent-hosted project backup isn't built yet — same "designed,
            # not wired up" status as agent-hosted cross-host restore (see
            # docs/MULTI_HOST_PLAN.md's M-Agent-2). Recorded as a failed
            # run rather than raised, so it's a failed run rather than a
            # BackupError, since one dead agent host shouldn't be any
            # different from any other per-run failure.
            _record_unsupported_project_agent_run(job_id, compose_project, job_host_id, batch_id)
            return batch_id
        _backup_project(
            job_id=job_id,
            job_host_id=job_host_id,
            compose_project=compose_project,
            job_dest_subpath=job_dest_subpath,
            job_include_bind_mounts=job_include_bind_mounts,
            target=target,
            batch_id=batch_id,
        )
        _apply_retention(job_id, compose_project)
        return batch_id

    for identity_key in identity_keys:
        if job_host_id == "local":
            _backup_one_container(
                job_id=job_id,
                job_host_id=job_host_id,
                identity_key=identity_key,
                job_dest_subpath=job_dest_subpath,
                job_include_bind_mounts=job_include_bind_mounts,
                target=target,
                batch_id=batch_id,
            )
            _apply_retention(job_id, identity_key)
        else:
            # Agent-hosted job: dispatch and move on — completion arrives
            # asynchronously via ws_agent.py's read loop (backup.stage/
            # chunk/complete/error), which calls _apply_retention() itself
            # once the transfer finishes. See agent_backup.py.
            _dispatch_agent_backup(
                job_id=job_id,
                job_host_id=job_host_id,
                identity_key=identity_key,
                job_dest_subpath=job_dest_subpath,
                job_include_bind_mounts=job_include_bind_mounts,
                target=target,
                batch_id=batch_id,
            )
    return batch_id


def _record_unsupported_project_agent_run(job_id: int, compose_project: str, job_host_id: str, batch_id: str) -> int:
    with get_session() as session:
        run = BackupRun(
            job_id=job_id,
            identity_key=compose_project,
            batch_id=batch_id,
            status="failed",
            current_stage=None,
            error_message=f"Project-scoped backup isn't supported for agent host {job_host_id!r} yet",
            finished_at=datetime.now(timezone.utc),
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id


def _dispatch_agent_backup(
    job_id: int,
    job_host_id: str,
    identity_key: str,
    job_dest_subpath: str,
    job_include_bind_mounts: bool,
    target: StorageTarget,
    batch_id: str,
) -> int:
    """Creates the BackupRun row, then hands off to the agent named
    job_host_id over its live WS connection. Never sends the target's
    credentials — the agent only ever learns which container to back up;
    the finished archive streams back to us and we alone call
    build_backend(target).put() (see agent_backup.handle_complete)."""
    from .agent_backup import register_pending_run

    with get_session() as session:
        container_row = (
            session.query(DiscoveredContainer).filter_by(host_id=job_host_id, identity_key=identity_key).first()
        )
        run = BackupRun(
            job_id=job_id, identity_key=identity_key, batch_id=batch_id, status="running", current_stage="requested"
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id
        docker_id = container_row.docker_id if container_row else None

    def fail(message: str) -> int:
        log.error("Agent backup run %s (container %s) failed: %s", run_id, identity_key, message)
        with get_session() as session:
            run = session.get(BackupRun, run_id)
            run.status = "failed"
            run.error_message = message
            run.finished_at = datetime.now(timezone.utc)
            session.commit()
        return run_id

    if docker_id is None:
        return fail(f"No discovered container for identity_key={identity_key!r} on agent {job_host_id!r}")

    register_pending_run(
        run_id, job_id=job_id, target_id=target.id, dest_subpath=job_dest_subpath, identity_key=identity_key
    )
    try:
        get_agent_registry().send_from_thread(
            job_host_id,
            {
                "type": "backup.request",
                "run_id": run_id,
                "docker_id": docker_id,
                "identity_key": identity_key,
                "host_id": job_host_id,
                "include_bind_mounts": job_include_bind_mounts,
            },
        )
    except AgentOfflineError as exc:
        return fail(str(exc))
    return run_id


def _backup_one_container(
    job_id: int,
    job_host_id: str,
    identity_key: str,
    job_dest_subpath: str,
    job_include_bind_mounts: bool,
    target: StorageTarget,
    batch_id: str,
) -> int:
    with get_session() as session:
        container_row = (
            session.query(DiscoveredContainer).filter_by(host_id=job_host_id, identity_key=identity_key).first()
        )
        run = BackupRun(
            job_id=job_id, identity_key=identity_key, batch_id=batch_id, status="running", current_stage="inspecting"
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id
        docker_id = container_row.docker_id if container_row else None

    log_lines: list[str] = []

    def log_line(msg: str) -> None:
        log_lines.append(msg)
        log.info("[run %s] %s", run_id, msg)

    def set_stage(stage: str, current: int | None = None, total: int | None = None) -> None:
        """Committed immediately (its own short-lived session) so a
        concurrent GET .../runs poll sees it right away — this is what
        lets the UI show a live "inspecting -> archiving (2/3) ->
        packaging -> uploading -> done" backup process instead of just
        a static "running" spinner."""
        with get_session() as session:
            run = session.get(BackupRun, run_id)
            run.current_stage = stage
            run.progress_current = current
            run.progress_total = total
            session.commit()

    def fail(error: Exception) -> int:
        log.exception("Backup run %s (container %s) failed", run_id, identity_key)
        with get_session() as session:
            run = session.get(BackupRun, run_id)
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            run.error_message = str(error)
            run.log_text = "\n".join(log_lines)
            session.commit()
        return run_id

    if docker_id is None:
        return fail(BackupError(f"No discovered container for identity_key={identity_key!r}"))

    staging_local: Path | None = None
    try:
        endpoint = get_endpoint(job_host_id)
        client = endpoint.client()

        try:
            container = client.containers.get(docker_id)
        except docker.errors.NotFound:
            return fail(BackupError(f"Container {docker_id} no longer exists on this host"))
        attrs = inspect_one(client, container)

        staging_local = config.STAGING_DIR / str(run_id)
        staging_local.mkdir(parents=True, exist_ok=True)
        staging_host = endpoint.host_path_for_data(f"staging/{run_id}")

        archivable = [
            m
            for m in attrs["mounts_json"]
            if m["type"] in ("volume", "bind") and m["source"] and (m["type"] != "bind" or job_include_bind_mounts)
        ]
        set_stage("archiving", current=0, total=len(archivable))

        data_manifest = []
        archived_count = 0
        for mount in attrs["mounts_json"]:
            if mount["type"] not in ("volume", "bind"):
                log_line(f"Skipping {mount['type']} mount {mount['destination']} (no persistent data)")
                continue
            if mount["type"] == "bind" and not job_include_bind_mounts:
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
            set_stage("archiving", current=archived_count, total=len(archivable))

        # Full volume entity data (driver, options, labels) — needed so
        # restore.py can recreate a missing named volume with the same
        # driver/options rather than just a bare default-driver volume.
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

        set_stage("packaging")
        config_json = {
            "schema_version": 1,
            "app_version": _app_version(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_host_id": job_host_id,
            "identity_key": identity_key,
            "docker_id": attrs["docker_id"],
            "container_name": attrs["name"],
            "compose": {
                "project": attrs["compose_project"],
                "service": attrs["compose_service"],
                "working_dir": attrs["compose_working_dir"],
                "config_files": attrs["compose_config_files"],
                "file_available": False,  # compose_resolver.py lands with HOSTFS_ENABLED support
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

        size_bytes = final_path.stat().st_size
        set_stage("uploading")
        backend = build_backend(target)
        dest_relpath = f"{job_dest_subpath}/{archive_name}".lstrip("/")
        locator = backend.put(final_path, dest_relpath)
        log_line(f"Stored archive as {locator!r} on target {target.name!r} ({size_bytes} bytes)")

        with get_session() as session:
            run = session.get(BackupRun, run_id)
            run.status = "success"
            run.current_stage = "done"
            run.finished_at = datetime.now(timezone.utc)
            run.archive_locator = locator
            run.size_bytes = size_bytes
            run.log_text = "\n".join(log_lines)
            run.config_snapshot_json = config_json
            session.commit()
        return run_id

    except (ArchiveError, StorageError, docker.errors.DockerException, OSError) as exc:
        return fail(exc)
    finally:
        if staging_local is not None:
            shutil.rmtree(staging_local, ignore_errors=True)


def _backup_project(
    job_id: int,
    job_host_id: str,
    compose_project: str,
    job_dest_subpath: str,
    job_include_bind_mounts: bool,
    target: StorageTarget,
    batch_id: str,
) -> int:
    """Project-scope equivalent of _backup_one_container: discovers every
    CURRENT container belonging to `compose_project` straight from the
    Docker daemon (not the DiscoveredContainer table, so a service added
    to the compose file since the last discovery sync is still included),
    archives the UNION of their distinct volumes/binds — a volume mounted
    into several of the project's containers (e.g. a worker sharing media
    storage with the web service) is archived once, not once per
    container — into a single archive, and writes one config.json
    describing every member container (each with its own recreate_spec)
    so restore.py can recreate the whole stack together. Produces exactly
    one BackupRun, identity_key = the project name (never ambiguous with
    a container-mode identity_key: those always contain zero or one "/",
    a bare project name never does)."""
    with get_session() as session:
        run = BackupRun(
            job_id=job_id,
            identity_key=compose_project,
            batch_id=batch_id,
            status="running",
            current_stage="inspecting",
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    log_lines: list[str] = []

    def log_line(msg: str) -> None:
        log_lines.append(msg)
        log.info("[run %s] %s", run_id, msg)

    def set_stage(stage: str, current: int | None = None, total: int | None = None) -> None:
        with get_session() as session:
            run = session.get(BackupRun, run_id)
            run.current_stage = stage
            run.progress_current = current
            run.progress_total = total
            session.commit()

    def fail(error: Exception) -> int:
        log.exception("Backup run %s (project %s) failed", run_id, compose_project)
        with get_session() as session:
            run = session.get(BackupRun, run_id)
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            run.error_message = str(error)
            run.log_text = "\n".join(log_lines)
            session.commit()
        return run_id

    staging_local: Path | None = None
    try:
        endpoint = get_endpoint(job_host_id)
        client = endpoint.client()

        containers = client.containers.list(
            all=True, filters={"label": f"com.docker.compose.project={compose_project}"}
        )
        if not containers:
            return fail(BackupError(f"No containers currently found for compose project {compose_project!r}"))
        member_attrs = [inspect_one(client, c) for c in containers]

        staging_local = config.STAGING_DIR / str(run_id)
        staging_local.mkdir(parents=True, exist_ok=True)
        staging_host = endpoint.host_path_for_data(f"staging/{run_id}")

        # Union of every member's archivable mounts, deduped by the same
        # (kind, sanitized name) key used for the archive member filename
        # — first container to mention a given volume/bind "wins" and it
        # is archived exactly once, however many of the project's
        # containers also mount it.
        dedup_mounts: dict[str, dict] = {}
        for attrs in member_attrs:
            for m in attrs["mounts_json"]:
                if m["type"] not in ("volume", "bind"):
                    continue
                if m["type"] == "bind" and not job_include_bind_mounts:
                    continue
                if not m["source"]:
                    continue
                kind = "volumes" if m["type"] == "volume" else "bind"
                raw_name = m["name"] or m["source"]
                member_name = f"{kind}__{sanitize(raw_name)}"
                dedup_mounts.setdefault(member_name, {"kind": kind, "raw_name": raw_name, "mount": m})

        set_stage("archiving", current=0, total=len(dedup_mounts))
        data_manifest = []
        for i, (member_name, entry) in enumerate(dedup_mounts.items(), start=1):
            kind, raw_name, mount = entry["kind"], entry["raw_name"], entry["mount"]
            log_line(f"Archiving {mount['type']} mount {raw_name} -> {member_name}")
            tar_source_to_staging(client, mount["source"], staging_host, member_name, staging_local)
            data_manifest.append(
                {
                    "kind": kind,
                    "name": raw_name,
                    "destination": mount["destination"],
                    "archive_member": f"data/{kind}/{sanitize(raw_name)}.tar.gz",
                }
            )
            set_stage("archiving", current=i, total=len(dedup_mounts))

        # Union of volume entities (driver/options), deduped by name.
        volume_entities: dict[str, dict] = {}
        for attrs in member_attrs:
            for m in attrs["mounts_json"]:
                if m["type"] != "volume" or not m["name"] or m["name"] in volume_entities:
                    continue
                try:
                    vol_attrs = client.volumes.get(m["name"]).attrs
                    volume_entities[m["name"]] = {
                        "name": vol_attrs.get("Name"),
                        "driver": vol_attrs.get("Driver"),
                        "options": vol_attrs.get("Options"),
                        "labels": vol_attrs.get("Labels"),
                    }
                except docker.errors.NotFound:
                    log_line(f"Volume {m['name']} vanished mid-backup, skipping its entity data")

        # Union of networks, deduped by name.
        network_entities: dict[str, dict] = {}
        for attrs in member_attrs:
            for n in attrs["networks_json"]:
                network_entities.setdefault(n["name"], n)

        set_stage("packaging")
        containers_json = [
            {
                "identity_key": attrs["identity_key"],
                "docker_id": attrs["docker_id"],
                "container_name": attrs["name"],
                "compose_service": attrs["compose_service"],
                "recreate_spec": _build_recreate_spec(attrs),
            }
            for attrs in member_attrs
        ]
        config_json = {
            "schema_version": 1,
            "scope": "project",
            "app_version": _app_version(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_host_id": job_host_id,
            "compose_project": compose_project,
            "compose": {
                "working_dir": member_attrs[0]["compose_working_dir"],
                "config_files": member_attrs[0]["compose_config_files"],
                "file_available": False,
            },
            "networks": list(network_entities.values()),
            "volumes": list(volume_entities.values()),
            "containers": containers_json,
            "data_manifest": data_manifest,
        }
        config_path = staging_local / "config.json"
        config_path.write_text(json.dumps(config_json, indent=2, default=str))

        archive_name = f"{sanitize(compose_project)}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.tar.gz"
        final_path = staging_local / archive_name
        with tarfile.open(final_path, "w:gz") as tf:
            tf.add(config_path, arcname="config.json")
            for item in data_manifest:
                member_path = staging_local / f"{item['kind']}__{sanitize(item['name'])}.tar.gz"
                tf.add(member_path, arcname=item["archive_member"])

        size_bytes = final_path.stat().st_size
        set_stage("uploading")
        backend = build_backend(target)
        dest_relpath = f"{job_dest_subpath}/{archive_name}".lstrip("/")
        locator = backend.put(final_path, dest_relpath)
        log_line(
            f"Stored archive as {locator!r} on target {target.name!r} "
            f"({size_bytes} bytes, {len(containers_json)} containers, {len(data_manifest)} volumes/binds)"
        )

        with get_session() as session:
            run = session.get(BackupRun, run_id)
            run.status = "success"
            run.current_stage = "done"
            run.finished_at = datetime.now(timezone.utc)
            run.archive_locator = locator
            run.size_bytes = size_bytes
            run.log_text = "\n".join(log_lines)
            run.config_snapshot_json = config_json
            session.commit()
        return run_id

    except (ArchiveError, StorageError, docker.errors.DockerException, OSError) as exc:
        return fail(exc)
    finally:
        if staging_local is not None:
            shutil.rmtree(staging_local, ignore_errors=True)


def _app_version() -> str:
    try:
        from importlib.metadata import version

        return version("containersafe")
    except Exception:
        return "unknown"


def _apply_retention(job_id: int, identity_key: str) -> None:
    """Keeps the newest `retention_count` successful runs for ONE
    container within a job, deleting the archive (via the job's current
    storage target) and the BackupRun row for anything older. Scoped to
    (job_id, identity_key) — not just job_id — so a multi-container job's
    containers each keep their own retention window instead of competing
    for one shared count. Time-based retention_days and remote (rclone)
    pruning land alongside real remote storage in a later pass."""
    with get_session() as session:
        job = session.get(BackupJob, job_id)
        if job is None or not job.retention_count:
            return
        target = session.get(StorageTarget, job.storage_target_id)
        runs = (
            session.query(BackupRun)
            .filter_by(job_id=job_id, identity_key=identity_key, status="success")
            .order_by(BackupRun.started_at.desc())
            .all()
        )
        to_prune = runs[job.retention_count :]
        if not to_prune or target is None:
            return
        backend = build_backend(target)
        for run in to_prune:
            if run.archive_locator:
                try:
                    backend.delete(run.archive_locator)
                except Exception:
                    log.warning("Failed to delete pruned archive %r for run %s", run.archive_locator, run.id)
            session.delete(run)
        session.commit()
