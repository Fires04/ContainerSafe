import uuid
from datetime import datetime, timedelta, timezone

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, BackgroundTasks, HTTPException

from .. import db, scheduler
from ..backup import run_backup_job
from ..models import BackupJob, BackupRun, DiscoveredContainer, StorageTarget
from ..schemas import BackupBatchOut, BackupJobIn, BackupJobOut, BackupRunOut, CronPreviewOut

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _validate_cron(expr: str) -> None:
    try:
        CronTrigger.from_crontab(expr)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid cron expression {expr!r}: {exc}") from None


def _resolve_containers(session, host_id: str, identity_keys: list[str]) -> dict[str, DiscoveredContainer]:
    rows = (
        session.query(DiscoveredContainer)
        .filter(DiscoveredContainer.host_id == host_id, DiscoveredContainer.identity_key.in_(identity_keys))
        .all()
    )
    by_key = {r.identity_key: r for r in rows}
    missing = [k for k in identity_keys if k not in by_key]
    if missing:
        raise HTTPException(404, f"No discovered container(s) for: {', '.join(missing)}")
    return by_key


def _to_out(row: BackupJob, target_name: str | None, container_names: list[str]) -> BackupJobOut:
    return BackupJobOut(
        id=row.id,
        host_id=row.host_id,
        identity_keys=list(row.identity_keys_json or []),
        container_names=container_names,
        display_name=row.display_name,
        schedule_cron=row.schedule_cron,
        retention_count=row.retention_count,
        retention_days=row.retention_days,
        storage_target_id=row.storage_target_id,
        storage_target_name=target_name,
        dest_subpath=row.dest_subpath,
        include_bind_mounts=row.include_bind_mounts,
        enabled=row.enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
        next_run_at=scheduler.get_next_run(row.id) if row.enabled else None,
    )


@router.get("/cron-preview", response_model=CronPreviewOut)
def cron_preview(expr: str, count: int = 3):
    """Lets the UI show "this cron means: next runs at ..." live as the
    user types, using the exact same CronTrigger that actually schedules
    jobs (scheduler.py) — not a re-implementation that could disagree
    with it on some edge case."""
    try:
        trigger = CronTrigger.from_crontab(expr)
    except ValueError as exc:
        return CronPreviewOut(valid=False, error=str(exc), next_runs=[])

    next_runs: list[datetime] = []
    current = datetime.now(timezone.utc)
    for _ in range(max(1, min(count, 10))):
        nxt = trigger.get_next_fire_time(None, current)
        if nxt is None:
            break
        next_runs.append(nxt)
        current = nxt + timedelta(seconds=1)
    return CronPreviewOut(valid=True, error=None, next_runs=next_runs)


@router.get("", response_model=list[BackupJobOut])
def list_jobs():
    with db.get_session() as session:
        rows = session.query(BackupJob).order_by(BackupJob.display_name).all()
        targets = {t.id: t.name for t in session.query(StorageTarget).all()}
        # Scoped by (host_id, identity_key), not identity_key alone —
        # the same identity_key can exist on more than one host (two
        # agents each happening to run a same-named container), so a
        # global-by-identity_key lookup could silently pick the wrong
        # host's container name.
        all_containers = session.query(DiscoveredContainer).all()
        names = {(c.host_id, c.identity_key): c.name for c in all_containers}
        return [
            _to_out(
                r,
                targets.get(r.storage_target_id),
                [names.get((r.host_id, k), k) for k in (r.identity_keys_json or [])],
            )
            for r in rows
        ]


@router.post("", response_model=BackupJobOut, status_code=201)
def create_job(payload: BackupJobIn):
    _validate_cron(payload.schedule_cron)
    with db.get_session() as session:
        containers = _resolve_containers(session, payload.host_id, payload.identity_keys)
        target = session.get(StorageTarget, payload.storage_target_id)
        if target is None:
            raise HTTPException(404, "Storage target not found")

        default_name = containers[payload.identity_keys[0]].name
        if len(payload.identity_keys) > 1:
            default_name += f" +{len(payload.identity_keys) - 1} more"

        row = BackupJob(
            host_id=payload.host_id,
            identity_keys_json=payload.identity_keys,
            display_name=payload.display_name or default_name,
            schedule_cron=payload.schedule_cron,
            retention_count=payload.retention_count,
            retention_days=payload.retention_days,
            storage_target_id=payload.storage_target_id,
            dest_subpath=payload.dest_subpath,
            include_bind_mounts=payload.include_bind_mounts,
            enabled=payload.enabled,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        scheduler.resync()
        return _to_out(row, target.name, [c.name for c in containers.values()])


@router.put("/{job_id}", response_model=BackupJobOut)
def update_job(job_id: int, payload: BackupJobIn):
    _validate_cron(payload.schedule_cron)
    with db.get_session() as session:
        row = session.get(BackupJob, job_id)
        if row is None:
            raise HTTPException(404, "Not found")
        containers = _resolve_containers(session, row.host_id, payload.identity_keys)
        target = session.get(StorageTarget, payload.storage_target_id)
        if target is None:
            raise HTTPException(404, "Storage target not found")

        row.identity_keys_json = payload.identity_keys
        row.display_name = payload.display_name or row.display_name
        row.schedule_cron = payload.schedule_cron
        row.retention_count = payload.retention_count
        row.retention_days = payload.retention_days
        row.storage_target_id = payload.storage_target_id
        row.dest_subpath = payload.dest_subpath
        row.include_bind_mounts = payload.include_bind_mounts
        row.enabled = payload.enabled
        session.commit()
        session.refresh(row)
        scheduler.resync()
        return _to_out(row, target.name, [c.name for c in containers.values()])


@router.delete("/{job_id}", status_code=204)
def delete_job(job_id: int):
    with db.get_session() as session:
        row = session.get(BackupJob, job_id)
        if row is None:
            raise HTTPException(404, "Not found")
        # SQLite reuses a deleted row's integer id for the next insert —
        # without this, a future job could silently "inherit" an unrelated
        # deleted job's run history just by landing on the same id.
        session.query(BackupRun).filter_by(job_id=job_id).delete()
        session.delete(row)
        session.commit()
    scheduler.resync()


@router.post("/{job_id}/run-now", response_model=BackupBatchOut)
def run_now(job_id: int, background_tasks: BackgroundTasks):
    """Kicks the batch off in the background and returns immediately with
    its batch_id — the UI polls GET /{job_id}/runs and filters by
    batch_id to show live per-container progress (see BackupRun's
    current_stage/progress_current/progress_total, updated incrementally
    by backup.py) while it runs, rather than the request blocking until
    the whole multi-container batch finishes. scheduler.py's own
    scheduled fires call run_backup_job() directly the same way, just
    without an HTTP request waiting on it at all."""
    with db.get_session() as session:
        job = session.get(BackupJob, job_id)
        if job is None:
            raise HTTPException(404, "Not found")
        identity_keys = list(job.identity_keys_json or [])
        if not identity_keys:
            raise HTTPException(400, "This job has no member containers")

    batch_id = uuid.uuid4().hex
    background_tasks.add_task(run_backup_job, job_id, batch_id)
    return BackupBatchOut(batch_id=batch_id, expected_count=len(identity_keys), runs=[])


@router.get("/{job_id}/runs", response_model=list[BackupRunOut])
def list_runs(job_id: int):
    with db.get_session() as session:
        rows = (
            session.query(BackupRun)
            .filter_by(job_id=job_id)
            .order_by(BackupRun.started_at.desc())
            .limit(50)
            .all()
        )
        return [BackupRunOut.model_validate(r) for r in rows]
