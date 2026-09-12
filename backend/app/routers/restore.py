import os
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import config, db
from ..models import BackupJob, BackupRun, RestoreRun, StorageTarget
from ..restore import RestoreError, discard_upload, restore_backup_run, restore_from_upload, stage_upload
from ..schemas import RestoreRequest, RestoreRunOut, UploadStageResult
from ..storage import build_backend

router = APIRouter(prefix="/api", tags=["restore"])


@router.post("/runs/{run_id}/restore", response_model=RestoreRunOut)
def restore_run(run_id: int, payload: RestoreRequest | None = None):
    payload = payload or RestoreRequest()
    try:
        restore_id = restore_backup_run(
            run_id, container_name_override=payload.container_name, allow_takeover=payload.allow_takeover
        )
    except RestoreError as exc:
        raise HTTPException(400, str(exc)) from None
    with db.get_session() as session:
        run = session.get(RestoreRun, restore_id)
        return RestoreRunOut.model_validate(run)


@router.get("/runs/{run_id}/download")
def download_run(run_id: int, background_tasks: BackgroundTasks):
    """Fetches the archive for a completed run back from its storage
    target (works for local AND rclone targets — same build_backend()
    used everywhere else) and streams it to the browser, so the operator
    can grab a copy without going near the host filesystem."""
    with db.get_session() as session:
        run = session.get(BackupRun, run_id)
        if run is None or run.status != "success" or not run.archive_locator:
            raise HTTPException(404, "No archive available for this run")
        job = session.get(BackupJob, run.job_id)
        target = session.get(StorageTarget, job.storage_target_id) if job else None
        if target is None:
            raise HTTPException(404, "This run's storage target no longer exists")
        archive_locator = run.archive_locator

    fd, tmp_path_str = tempfile.mkstemp(suffix=".tar.gz", dir=config.RESTORE_TMP_DIR)
    os.close(fd)
    tmp_path = Path(tmp_path_str)
    try:
        build_backend(target).get(archive_locator, tmp_path)
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(502, f"Failed to fetch archive from target: {exc}") from None

    background_tasks.add_task(tmp_path.unlink, missing_ok=True)
    filename = Path(archive_locator).name
    return FileResponse(tmp_path, media_type="application/gzip", filename=filename, background=background_tasks)


@router.get("/restores", response_model=list[RestoreRunOut])
def list_restores():
    with db.get_session() as session:
        rows = session.query(RestoreRun).order_by(RestoreRun.started_at.desc()).limit(20).all()
        return [RestoreRunOut.model_validate(r) for r in rows]


# --- Upload wizard: step 1 (upload -> preview), step 2 (confirm/discard) ---


@router.post("/restore/upload", response_model=UploadStageResult)
def upload_restore_archive(file: UploadFile = File(...)):
    if not (file.filename or "").endswith((".tar.gz", ".tgz")):
        raise HTTPException(400, "Expected a .tar.gz / .tgz archive")

    config.RESTORE_TMP_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = config.RESTORE_TMP_DIR / f"incoming-{uuid.uuid4().hex}.tar.gz"
    try:
        with tmp_path.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        try:
            result = stage_upload(tmp_path)
        except RestoreError as exc:
            raise HTTPException(400, str(exc)) from None
    finally:
        tmp_path.unlink(missing_ok=True)
    return result


@router.post("/restore/upload/{upload_id}/confirm", response_model=RestoreRunOut)
def confirm_restore_upload(upload_id: str, payload: RestoreRequest | None = None):
    payload = payload or RestoreRequest()
    try:
        restore_id = restore_from_upload(
            upload_id, container_name_override=payload.container_name, allow_takeover=payload.allow_takeover
        )
    except RestoreError as exc:
        raise HTTPException(400, str(exc)) from None
    with db.get_session() as session:
        run = session.get(RestoreRun, restore_id)
        return RestoreRunOut.model_validate(run)


@router.delete("/restore/upload/{upload_id}", status_code=204)
def cancel_restore_upload(upload_id: str):
    discard_upload(upload_id)
