"""SQLAlchemy ORM models. MVP uses Base.metadata.create_all() (see db.py) —
no Alembic yet, add it once the schema stops changing every milestone.

Every table that's scoped to a particular Docker daemon carries a
`host_id` column, always "local" for now (see docker_endpoint.py) — this
is the seam for a future multi-host/agent model: nothing here needs a
migration when a second host_id shows up, the column already exists.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DiscoveredContainer(Base):
    """One row per container ever seen on a given host, keyed by a
    STABLE identity (compose project/service, or container name) rather
    than the Docker container ID, which changes every time the container
    is recreated (redeploy, image update, `docker compose up` again).
    BackupJob rows reference `identity_key`, not `docker_id`, so a job
    configured once keeps working across redeploys.
    """

    __tablename__ = "discovered_containers"
    __table_args__ = (UniqueConstraint("host_id", "identity_key", name="uq_container_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host_id: Mapped[str] = mapped_column(String, default="local")
    identity_key: Mapped[str] = mapped_column(String, index=True)
    docker_id: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String)
    image: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="unknown")

    compose_project: Mapped[str | None] = mapped_column(String, nullable=True)
    compose_service: Mapped[str | None] = mapped_column(String, nullable=True)
    compose_working_dir: Mapped[str | None] = mapped_column(String, nullable=True)
    compose_config_files: Mapped[str | None] = mapped_column(String, nullable=True)

    labels_json: Mapped[dict] = mapped_column(JSON, default=dict)
    mounts_json: Mapped[list] = mapped_column(JSON, default=list)
    networks_json: Mapped[list] = mapped_column(JSON, default=list)
    env_json: Mapped[list] = mapped_column(JSON, default=list)
    ports_json: Mapped[dict] = mapped_column(JSON, default=dict)
    restart_policy_json: Mapped[dict] = mapped_column(JSON, default=dict)
    command_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    entrypoint_json: Mapped[list | None] = mapped_column(JSON, nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class StorageTarget(Base):
    __tablename__ = "storage_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    type: Mapped[str] = mapped_column(String)  # "local" | "rclone"
    # Where "local" physically writes: "local" = this server's own
    # ./backup; any other value = that Agent's own host_id, for a future
    # agent-local target (write on the agent's own disk, never touching
    # the server) — not wired up yet, see docs/MULTI_HOST_PLAN.md. rclone
    # targets are always server-side regardless of this column, since
    # their credentials never leave the server.
    host_id: Mapped[str] = mapped_column(String, default="local")
    config_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class BackupJob(Base):
    __tablename__ = "backup_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host_id: Mapped[str] = mapped_column(String, default="local")
    # "container": identity_keys_json lists the specific containers this
    # job backs up (today's original model) — one BackupRun per container
    # per firing, grouped by batch_id. "project": compose_project names a
    # compose project instead; identity_keys_json is unused/empty. At run
    # time ALL of that project's current containers are discovered fresh
    # and backed up together into ONE archive (shared volumes/binds
    # deduped so a volume mounted into several of the project's
    # containers — common for e.g. a worker + web service sharing media
    # storage — is archived once, not once per container), producing a
    # single BackupRun per firing (identity_key = the project name, never
    # ambiguous with a container-mode identity_key: those are either a
    # bare name or "project/service", always containing no slash or one).
    # New services added to the compose project later are picked up
    # automatically next run — nothing to reconfigure.
    scope_type: Mapped[str] = mapped_column(String, default="container")
    compose_project: Mapped[str | None] = mapped_column(String, nullable=True)
    # List of DiscoveredContainer.identity_key this job backs up together —
    # one job, many containers, one BackupRun per container per firing
    # (see backup.py's batch_id). Migrated from a singular, NOT-NULL
    # `identity_key` column (see migrations.py) — that old column is
    # actually DROPPED by the migration, not just left unmapped, since an
    # ORM insert that never sets it would otherwise violate its NOT NULL
    # constraint.
    identity_keys_json: Mapped[list] = mapped_column(JSON, default=list)
    display_name: Mapped[str] = mapped_column(String)
    schedule_cron: Mapped[str] = mapped_column(String)  # standard 5-field cron
    retention_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_target_id: Mapped[int] = mapped_column(ForeignKey("storage_targets.id"))
    dest_subpath: Mapped[str] = mapped_column(String, default="")
    include_bind_mounts: Mapped[bool] = mapped_column(default=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class BackupRun(Base):
    __tablename__ = "backup_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("backup_jobs.id"))
    # Which single container this run backed up (a job can list several —
    # see BackupJob.identity_keys_json). Migrated/backfilled from the
    # job's old singular identity_key, see migrations.py.
    identity_key: Mapped[str] = mapped_column(String, default="")
    # Groups every BackupRun produced by one firing of a multi-container
    # job (manual "Run now" or a scheduled fire) so the UI can show them
    # together; None for runs from before this existed.
    batch_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # Live progress, updated incrementally by backup.py as the run
    # proceeds (not just set once at the end) so the UI can poll and show
    # a real stage-by-stage backup process: "inspecting" -> "archiving"
    # (with progress_current/progress_total = which mount, out of how
    # many) -> "packaging" -> "uploading" -> "done". Stays at whatever
    # stage it reached on failure, so the UI can show where it died.
    current_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    progress_current: Mapped[int | None] = mapped_column(Integer, nullable=True)
    progress_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|running|success|failed
    archive_locator: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log_text: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    config_snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class Agent(Base):
    """A remote host's ContainerSafe agent (see agent/), enrolled
    once via POST /api/agents. `host_id` here is what every other
    host_id-scoped table's `host_id` column references once containers
    on this agent's host get discovered. Live connection state (the
    actual open WebSocket) lives only in agents/registry.py's in-memory
    registry — this row is the durable/UI-facing half. `online` is
    deliberately derived from connected_at alone (no separate status
    enum to drift out of sync).
    """

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    host_id: Mapped[str] = mapped_column(String, unique=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    token_prefix: Mapped[str] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_rtt_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_version: Mapped[str | None] = mapped_column(String, nullable=True)

    @property
    def online(self) -> bool:
        return self.connected_at is not None


class RestoreRun(Base):
    __tablename__ = "restore_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_archive_locator: Mapped[str] = mapped_column(String)
    target_identity_key: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")
    log_text: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
