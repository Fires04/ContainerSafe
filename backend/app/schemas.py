from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator


class ContainerOut(BaseModel):
    id: int
    host_id: str
    identity_key: str
    docker_id: str
    name: str
    image: str
    status: str
    compose_project: str | None
    compose_service: str | None
    compose_working_dir: str | None
    compose_config_files: str | None
    labels_json: dict
    mounts_json: list
    networks_json: list
    env_json: list
    ports_json: dict
    restart_policy_json: dict
    command_json: list | None
    entrypoint_json: list | None
    first_seen_at: datetime
    last_seen_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("env_json", mode="before")
    @classmethod
    def _strip_env_values(cls, value: list) -> list:
        """Never send env VALUES to the browser over this API — only the
        variable names. Storage (DiscoveredContainer.env_json in the DB)
        and internal use (backup.py's recreate_spec, built straight from
        discovery.inspect_one's own dict, not through this schema) still
        keep full values; this trims only what leaves the server here."""
        return [str(item).split("=", 1)[0] for item in (value or [])]


class AgentIn(BaseModel):
    host_id: str


class AgentOut(BaseModel):
    id: int
    host_id: str
    online: bool
    token_prefix: str
    created_at: datetime
    connected_at: datetime | None
    last_seen_at: datetime | None
    last_heartbeat_rtt_ms: int | None
    agent_version: str | None

    model_config = {"from_attributes": True}


class AgentEnrollResult(BaseModel):
    agent: AgentOut
    token: str
    env_snippet: str
    install_url: str
    curl_command: str


class StorageTargetIn(BaseModel):
    name: str
    type: Literal["local", "rclone"]
    # local: {"path": "<subdir under /app/backup, may be empty>"}
    # rclone: {"remote_type": "sftp"|"smb"|"s3"|"webdav", "host":, "port":,
    #          "user":, "password":, "path":, ...} — "password" (plaintext,
    #          write-only) is converted to "pass_obscured" by the router
    #          and never itself persisted.
    config: dict


class StorageTargetOut(BaseModel):
    id: int
    name: str
    type: str
    config_json: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class BackupJobIn(BaseModel):
    # Explicit, not inferred: identity_key alone can collide across hosts
    # (e.g. two agents each happening to run a same-named compose
    # project/service, or just two unrelated containers sharing a name) —
    # the picker UI already knows which host a container came from when
    # the operator selects it, so it sends that along rather than the
    # server guessing.
    host_id: str = "local"
    # "container" (default): identity_keys names the specific containers.
    # "project": compose_project names a compose project instead — ALL of
    # its current containers are backed up together as one unit (see
    # models.py's BackupJob docstring); identity_keys is ignored.
    scope_type: Literal["container", "project"] = "container"
    identity_keys: list[str] = []
    compose_project: str | None = None
    display_name: str | None = None
    schedule_cron: str
    retention_count: int | None = None
    retention_days: int | None = None
    storage_target_id: int
    dest_subpath: str = ""
    include_bind_mounts: bool = True
    enabled: bool = True

    @model_validator(mode="after")
    def _scope_matches_selection(self) -> "BackupJobIn":
        if self.scope_type == "project":
            if not self.compose_project:
                raise ValueError("A project-scoped job needs a compose project")
        elif not self.identity_keys:
            raise ValueError("A job needs at least one container")
        return self


class BackupJobOut(BaseModel):
    id: int
    host_id: str
    scope_type: str = "container"
    compose_project: str | None = None
    identity_keys: list[str]
    container_names: list[str] = []
    display_name: str
    schedule_cron: str
    retention_count: int | None
    retention_days: int | None
    storage_target_id: int
    storage_target_name: str | None = None
    dest_subpath: str
    include_bind_mounts: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime
    next_run_at: datetime | None = None


class BackupRunOut(BaseModel):
    id: int
    job_id: int
    identity_key: str
    batch_id: str | None
    current_stage: str | None = None
    progress_current: int | None = None
    progress_total: int | None = None
    started_at: datetime
    finished_at: datetime | None
    status: str
    archive_locator: str | None
    size_bytes: int | None
    log_text: str | None
    error_message: str | None
    job_display_name: str | None = None  # only populated by GET /api/runs/recent

    model_config = {"from_attributes": True}


class BackupBatchOut(BaseModel):
    batch_id: str
    expected_count: int
    runs: list[BackupRunOut]


class CronPreviewOut(BaseModel):
    valid: bool
    error: str | None
    next_runs: list[datetime]


class RestoreRequest(BaseModel):
    container_name: str | None = None
    allow_takeover: bool = False


class UploadPreview(BaseModel):
    scope: Literal["container", "project"] = "container"
    # project scope: one summary dict per member container
    # ({identity_key, container_name, image, name_conflict,
    # name_conflict_running}); None/unused for scope=="container".
    containers: list[dict] | None = None
    identity_key: str | None
    container_name: str | None
    image: str | None
    created_at: str | None
    app_version: str | None
    schema_version: int | None
    compose: dict | None
    networks: list[dict]
    volumes: list[dict]
    mounts: list[dict]
    data_manifest: list[dict]
    ports: dict | None
    restart_policy: dict | None
    name_conflict: bool | None
    name_conflict_running: bool | None


class UploadStageResult(BaseModel):
    upload_id: str
    preview: UploadPreview


class RestoreRunOut(BaseModel):
    id: int
    source_archive_locator: str
    target_identity_key: str | None
    started_at: datetime
    finished_at: datetime | None
    status: str
    log_text: str | None
    error_message: str | None

    model_config = {"from_attributes": True}
