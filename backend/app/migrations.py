"""Hand-written, idempotent startup migrations — no Alembic yet (MVP,
schema still young). Each function checks the live SQLite schema via
PRAGMA table_info before touching anything, so re-running on an
already-migrated DB is a no-op. Called once from db.init_db(), after
Base.metadata.create_all() (which only creates missing TABLES, never adds
columns to ones that already exist — that's what this module is for).
"""

import json
import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

log = logging.getLogger("containersafe.migrations")


def _columns(engine: Engine, table: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}  # row[1] is the column name


def _add_identity_keys_json(engine: Engine) -> None:
    """backup_jobs.identity_key (singular) -> identity_keys_json (list).
    Backfills but does NOT yet drop the old column — that happens in
    _drop_backup_jobs_identity_key(), only after backup_runs has also
    had its chance to backfill from it (see run_startup_migrations'
    ordering)."""
    cols = _columns(engine, "backup_jobs")
    if "identity_key" not in cols or "identity_keys_json" in cols:
        return  # already migrated, or a fresh DB that never had the old column

    log.info("Migrating backup_jobs.identity_key -> identity_keys_json")
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE backup_jobs ADD COLUMN identity_keys_json TEXT"))
        rows = conn.execute(text("SELECT id, identity_key FROM backup_jobs")).fetchall()
        for row_id, old_key in rows:
            conn.execute(
                text("UPDATE backup_jobs SET identity_keys_json = :val WHERE id = :id"),
                {"val": json.dumps([old_key] if old_key else []), "id": row_id},
            )


def _add_backup_runs_identity_and_batch(engine: Engine) -> None:
    """backup_runs gains identity_key (backfilled from its job's OLD
    singular identity_key — must run before that column is dropped) and
    batch_id (nullable, only new multi-container runs set it)."""
    cols = _columns(engine, "backup_runs")
    job_cols = _columns(engine, "backup_jobs")
    changed = False

    with engine.begin() as conn:
        if "identity_key" not in cols:
            conn.execute(text("ALTER TABLE backup_runs ADD COLUMN identity_key TEXT DEFAULT ''"))
            changed = True
            if "identity_key" in job_cols:  # old singular column still present
                conn.execute(
                    text(
                        "UPDATE backup_runs SET identity_key = COALESCE("
                        "  (SELECT backup_jobs.identity_key FROM backup_jobs WHERE backup_jobs.id = backup_runs.job_id),"
                        "  ''"
                        ")"
                    )
                )
        if "batch_id" not in cols:
            conn.execute(text("ALTER TABLE backup_runs ADD COLUMN batch_id TEXT"))
            changed = True

    if changed:
        log.info("Migrated backup_runs: added identity_key (backfilled) / batch_id")


def _drop_backup_jobs_identity_key(engine: Engine) -> None:
    """Must actually drop this (not just leave it unmapped) — it's a
    NOT NULL column from the old schema, and the ORM's INSERT no longer
    sets it at all, so every new BackupJob insert would violate that
    constraint if the column stayed. Requires SQLite >= 3.35 (DROP COLUMN
    support, released 2021) — bundled with python:3.12-slim's libsqlite3
    is well past that (3.46+ as of this writing)."""
    cols = _columns(engine, "backup_jobs")
    if "identity_key" not in cols or "identity_keys_json" not in cols:
        return  # already dropped, or the backfill above hasn't run yet this pass
    log.info("Dropping obsolete backup_jobs.identity_key (superseded by identity_keys_json)")
    with engine.begin() as conn:
        # The old column was declared `index=True` — its index survives
        # a DROP COLUMN and then points at nothing, breaking the DROP
        # COLUMN itself ("error in index ... after drop column"). Drop
        # the index first.
        conn.execute(text("DROP INDEX IF EXISTS ix_backup_jobs_identity_key"))
        conn.execute(text("ALTER TABLE backup_jobs DROP COLUMN identity_key"))


def _add_backup_runs_progress(engine: Engine) -> None:
    """Live stage/progress columns for the "backup process" UI (stages:
    inspecting -> archiving -> packaging -> uploading -> done)."""
    cols = _columns(engine, "backup_runs")
    changed = False
    with engine.begin() as conn:
        if "current_stage" not in cols:
            conn.execute(text("ALTER TABLE backup_runs ADD COLUMN current_stage TEXT"))
            changed = True
        if "progress_current" not in cols:
            conn.execute(text("ALTER TABLE backup_runs ADD COLUMN progress_current INTEGER"))
            changed = True
        if "progress_total" not in cols:
            conn.execute(text("ALTER TABLE backup_runs ADD COLUMN progress_total INTEGER"))
            changed = True
    if changed:
        log.info("Migrated backup_runs: added current_stage / progress_current / progress_total")


def _add_storage_targets_host_id(engine: Engine) -> None:
    """Where a "local" target physically writes — "local" (the server
    itself, today's only real behavior) or a future agent's own host_id.
    `agents` itself is a brand-new table, created for free by
    Base.metadata.create_all(); only this ADD COLUMN is needed here."""
    cols = _columns(engine, "storage_targets")
    if "host_id" in cols:
        return
    log.info("Migrating storage_targets: added host_id (default 'local')")
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE storage_targets ADD COLUMN host_id TEXT DEFAULT 'local'"))
        conn.execute(text("UPDATE storage_targets SET host_id = 'local' WHERE host_id IS NULL"))


def run_startup_migrations(engine: Engine) -> None:
    _add_identity_keys_json(engine)
    _add_backup_runs_identity_and_batch(engine)  # must run before the drop below
    _drop_backup_jobs_identity_key(engine)
    _add_backup_runs_progress(engine)
    _add_storage_targets_host_id(engine)
