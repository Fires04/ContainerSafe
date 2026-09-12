from fastapi import APIRouter, HTTPException

from .. import db
from ..models import BackupJob, StorageTarget
from ..schemas import StorageTargetIn, StorageTargetOut
from ..storage.rclone import obscure_password

router = APIRouter(prefix="/api/targets", tags=["targets"])

def _normalize_config(payload: StorageTargetIn) -> dict:
    config = dict(payload.config)
    if payload.type == "local":
        config.setdefault("path", "")
        return config

    # rclone — "path" may legitimately be "" (root of the remote), so
    # only remote_type is actually required here.
    config.setdefault("path", "")
    password = config.pop("password", None)
    if password:
        config["pass_obscured"] = obscure_password(password)
    if not config.get("remote_type"):
        raise HTTPException(400, "rclone target missing required field: remote_type")
    return config


@router.get("", response_model=list[StorageTargetOut])
def list_targets():
    with db.get_session() as session:
        rows = session.query(StorageTarget).order_by(StorageTarget.name).all()
        return [StorageTargetOut.model_validate(r) for r in rows]


@router.post("", response_model=StorageTargetOut, status_code=201)
def create_target(payload: StorageTargetIn):
    config = _normalize_config(payload)
    with db.get_session() as session:
        if session.query(StorageTarget).filter_by(name=payload.name).first():
            raise HTTPException(409, "A target with that name already exists")
        row = StorageTarget(name=payload.name, type=payload.type, config_json=config)
        session.add(row)
        session.commit()
        session.refresh(row)
        return StorageTargetOut.model_validate(row)


@router.delete("/{target_id}", status_code=204)
def delete_target(target_id: int):
    with db.get_session() as session:
        row = session.get(StorageTarget, target_id)
        if row is None:
            raise HTTPException(404, "Not found")
        in_use = session.query(BackupJob).filter_by(storage_target_id=target_id).count()
        if in_use:
            raise HTTPException(
                409, f"Target is used by {in_use} backup job(s) — reassign or delete those first"
            )
        session.delete(row)
        session.commit()
