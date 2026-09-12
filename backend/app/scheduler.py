"""Runs enabled BackupJob rows on their stored cron schedule via
APScheduler. The `backup_jobs` DB table is the single source of truth —
this module keeps no persistent state of its own beyond what's currently
scheduled in memory, so a full resync from the DB is always safe and
cheap enough to do liberally (every CRUD mutation, plus a periodic
safety-net sweep in case a call site ever forgets to).
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .backup import BackupError, run_backup_job
from .db import get_session
from .models import BackupJob

log = logging.getLogger("containersafe.scheduler")

_JOB_PREFIX = "backup-job-"
scheduler = AsyncIOScheduler()


def _run_scheduled(job_id: int) -> None:
    try:
        batch_id = run_backup_job(job_id)
        log.info("Scheduled run for job %s finished as batch %s", job_id, batch_id)
    except BackupError as exc:
        # Job/target/container vanished between scheduling and firing —
        # logged, not raised; the next resync() drops it from the
        # schedule entirely if the job row itself is gone.
        log.error("Scheduled job %s could not start: %s", job_id, exc)


def resync() -> None:
    """Reconciles APScheduler's in-memory job set against backup_jobs.
    Call after any job create/update/delete/enable-toggle (routers/jobs.py
    does) so a schedule change takes effect immediately rather than
    waiting for the next periodic sweep."""
    with get_session() as session:
        rows = session.query(BackupJob).filter_by(enabled=True).all()
        desired: dict[str, CronTrigger] = {}
        for row in rows:
            try:
                desired[f"{_JOB_PREFIX}{row.id}"] = CronTrigger.from_crontab(row.schedule_cron)
            except ValueError:
                log.warning("Job %s has an invalid stored cron %r, skipping", row.id, row.schedule_cron)

    for job_id, trigger in desired.items():
        num = int(job_id.removeprefix(_JOB_PREFIX))
        scheduler.add_job(
            _run_scheduled,
            trigger=trigger,
            id=job_id,
            args=[num],
            replace_existing=True,
            misfire_grace_time=300,
            coalesce=True,
            max_instances=1,
        )

    stale = [j.id for j in scheduler.get_jobs() if j.id.startswith(_JOB_PREFIX) and j.id not in desired]
    for job_id in stale:
        scheduler.remove_job(job_id)


def get_next_run(job_id: int):
    """Returns the next scheduled fire time for a job, or None if it
    isn't currently scheduled (disabled, invalid cron, or the scheduler
    hasn't resynced yet)."""
    job = scheduler.get_job(f"{_JOB_PREFIX}{job_id}")
    return job.next_run_time if job else None


def start() -> None:
    resync()
    scheduler.add_job(resync, "interval", seconds=60, id="resync-jobs", replace_existing=True)
    scheduler.start()


def stop() -> None:
    scheduler.shutdown(wait=False)
