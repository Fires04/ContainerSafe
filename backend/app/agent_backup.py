"""Server-side handling of an agent-driven backup: receives backup.stage/
chunk/complete/error messages from ws_agent.py's read loop and drives the
same BackupRun lifecycle backup.py's _backup_one_container() drives for a
local job — just fed by an agent's assembled archive arriving in chunks
over the wire instead of being built in-process. See backup.py for the
dispatch side (_dispatch_agent_backup) and docs/MULTI_HOST_PLAN.md for
the wire format and full rationale (agents never see storage-target
credentials — archives always flow agent -> server -> target).

These handlers run synchronous/blocking I/O (file writes, DB sessions,
StorageBackend.put() — which for an rclone target shells out and can take
a while) — ws_agent.py's async read loop must call them via
asyncio.to_thread(), never awaited inline, or a slow upload would stall
every other agent/browser connection this process is serving.
"""

import base64
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .db import get_session
from .models import BackupJob, BackupRun, StorageTarget
from .storage import build_backend

log = logging.getLogger("containersafe.agent_backup")


@dataclass
class _PendingRun:
    job_id: int
    target_id: int
    dest_subpath: str
    identity_key: str
    tmp_path: Path


# In-memory only — this app is single-process, and a pending agent
# backup that outlives a server restart has no archive to resume anyway
# (the agent would need to re-run the whole backup from scratch, which is
# exactly what re-triggering the job does).
_pending: dict[int, _PendingRun] = {}


def register_pending_run(run_id: int, job_id: int, target_id: int, dest_subpath: str, identity_key: str) -> None:
    tmp_path = config.INCOMING_DIR / f"{run_id}.tar.gz"
    tmp_path.unlink(missing_ok=True)
    _pending[run_id] = _PendingRun(
        job_id=job_id, target_id=target_id, dest_subpath=dest_subpath, identity_key=identity_key, tmp_path=tmp_path
    )


def handle_stage(run_id: int, stage: str | None, current: int | None, total: int | None) -> None:
    """Mirrors backup.py's own set_stage() — writes straight into the
    same BackupRun columns the local path uses, so the UI's polling
    display needs zero changes to show an agent-driven run's progress."""
    with get_session() as session:
        run = session.get(BackupRun, run_id)
        if run is None:
            return
        run.current_stage = stage
        run.progress_current = current
        run.progress_total = total
        session.commit()


def handle_chunk(run_id: int, data_b64: str) -> None:
    pending = _pending.get(run_id)
    if pending is None:
        log.warning("backup.chunk for unknown/expired run %s, dropping", run_id)
        return
    with pending.tmp_path.open("ab") as f:
        f.write(base64.b64decode(data_b64))


def handle_complete(run_id: int, total_size: int) -> None:
    from .backup import _apply_retention  # local import: avoids a backup.py <-> agent_backup.py import cycle

    pending = _pending.pop(run_id, None)
    if pending is None:
        log.warning("backup.complete for unknown/expired run %s, ignoring", run_id)
        return
    try:
        with get_session() as session:
            target = session.get(StorageTarget, pending.target_id)
            job = session.get(BackupJob, pending.job_id)
        if target is None or job is None:
            raise RuntimeError("This run's job or storage target no longer exists")

        on_disk_size = pending.tmp_path.stat().st_size
        if total_size and on_disk_size != total_size:
            log.warning("run %s: agent reported %d bytes, received %d", run_id, total_size, on_disk_size)

        backend = build_backend(target)
        dest_relpath = f"{pending.dest_subpath}/{pending.tmp_path.name}".lstrip("/")
        locator = backend.put(pending.tmp_path, dest_relpath)

        with get_session() as session:
            run = session.get(BackupRun, run_id)
            run.status = "success"
            run.current_stage = "done"
            run.finished_at = datetime.now(timezone.utc)
            run.archive_locator = locator
            run.size_bytes = on_disk_size
            run.log_text = (
                f"Received {on_disk_size} bytes from agent {job.host_id!r}, "
                f"stored as {locator!r} on target {target.name!r}"
            )
            session.commit()

        _apply_retention(pending.job_id, pending.identity_key)
    except Exception as exc:
        log.exception("Failed to finalize agent backup run %s", run_id)
        with get_session() as session:
            run = session.get(BackupRun, run_id)
            if run is not None:
                run.status = "failed"
                run.error_message = f"Failed to store archive received from agent: {exc}"
                run.finished_at = datetime.now(timezone.utc)
                session.commit()
    finally:
        pending.tmp_path.unlink(missing_ok=True)


def handle_error(run_id: int, message: str) -> None:
    pending = _pending.pop(run_id, None)
    if pending is not None:
        pending.tmp_path.unlink(missing_ok=True)
    with get_session() as session:
        run = session.get(BackupRun, run_id)
        if run is None:
            return
        run.status = "failed"
        run.error_message = message
        run.finished_at = datetime.now(timezone.utc)
        session.commit()
