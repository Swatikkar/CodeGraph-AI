from fastapi import HTTPException

from models.user import IngestionJobModel
from utils.database import SessionLocal


class IngestionCancelled(RuntimeError):
    pass


def raise_if_cancelled(job_id: int | None) -> None:
    if not job_id:
        return
    db = SessionLocal()
    try:
        job = db.get(IngestionJobModel, job_id)
        if job and job.cancel_requested_at is not None:
            raise IngestionCancelled("Ingestion was cancelled by the user.")
    finally:
        db.close()


def validate_cancellable_status(status: str) -> None:
    if status not in {"queued", "retryable", "running"}:
        raise HTTPException(status_code=409, detail="Only active ingestion jobs can be cancelled.")
