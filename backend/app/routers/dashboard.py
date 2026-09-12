from fastapi import APIRouter

from .. import db
from ..models import BackupJob, BackupRun
from ..schemas import BackupRunOut

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/runs/recent", response_model=list[BackupRunOut])
def recent_runs(limit: int = 20):
    """Most recent BackupRun rows across every job, each carrying its
    job's display_name — feeds the Dashboard's "recent activity" list,
    which (unlike GET /api/jobs/{id}/runs) isn't scoped to one job."""
    with db.get_session() as session:
        rows = session.query(BackupRun).order_by(BackupRun.started_at.desc()).limit(limit).all()
        job_names = {j.id: j.display_name for j in session.query(BackupJob).all()}
        out = []
        for r in rows:
            item = BackupRunOut.model_validate(r)
            item.job_display_name = job_names.get(r.job_id)
            out.append(item)
        return out
