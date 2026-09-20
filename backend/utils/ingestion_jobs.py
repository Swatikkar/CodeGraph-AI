from datetime import datetime, timedelta, timezone
import threading
import time

from fastapi import HTTPException
from sqlalchemy.orm import Session

from config import settings
from graph import codegraph_app
from models.user import IngestionJobModel, ProjectModel
from utils.database import SessionLocal
from utils.observability import log_event
from utils.project_persistence import materialize_project_from_db, persist_project_snapshot, storage_enabled
from utils.storage import project_root, read_status, write_status


ACTIVE_JOB_STATES = {"queued", "running", "retryable"}
TERMINAL_JOB_STATES = {"completed", "failed"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue_ingestion_job(db: Session, project: ProjectModel) -> IngestionJobModel:
    job = (
        db.query(IngestionJobModel)
        .filter(IngestionJobModel.project_id == project.id)
        .with_for_update()
        .first()
    )
    if not job:
        job = IngestionJobModel(
            project_id=project.id,
            user_id=project.user_id,
            project_slug=project.slug,
        )
        db.add(job)
    elif job.status in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="This project already has an active ingestion job.")

    job.status = "queued"
    job.attempt_count = 0
    job.max_attempts = settings.INGESTION_JOB_MAX_ATTEMPTS
    job.heartbeat_at = None
    job.started_at = None
    job.finished_at = None
    job.error_code = None
    job.error_message = None
    project.status = "queued"
    project.stage = "queued"
    project.message = "Project is queued for analysis."
    project.progress = 8
    db.commit()
    db.refresh(job)
    return job


def _recover_stale_jobs(db: Session) -> int:
    cutoff = utc_now() - timedelta(seconds=settings.INGESTION_JOB_STALE_SECONDS)
    stale = db.query(IngestionJobModel).filter(
        IngestionJobModel.status == "running",
        IngestionJobModel.heartbeat_at < cutoff,
    ).all()
    for job in stale:
        if job.attempt_count < job.max_attempts:
            job.status = "retryable"
            job.error_code = "stale_worker"
            job.error_message = "Worker heartbeat expired; the job will be retried."
            job.project.status = "retryable"
            job.project.stage = "worker"
            job.project.message = "Interrupted analysis will be retried."
            job.project.error = job.error_code
        else:
            job.status = "failed"
            job.finished_at = utc_now()
            job.error_code = "attempts_exhausted"
            job.error_message = "Worker heartbeat expired and retry attempts were exhausted."
            job.project.status = "error"
            job.project.stage = "worker"
            job.project.message = "Ingestion failed after repeated worker interruptions."
            job.project.error = job.error_code
    if stale:
        db.commit()
    return len(stale)


def claim_next_job() -> int | None:
    db = SessionLocal()
    try:
        _recover_stale_jobs(db)
        query = db.query(IngestionJobModel).filter(IngestionJobModel.status.in_(("queued", "retryable")))
        if db.bind and db.bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        job = query.order_by(IngestionJobModel.created_at.asc(), IngestionJobModel.id.asc()).first()
        if not job:
            return None
        job.status = "running"
        job.attempt_count += 1
        job.started_at = job.started_at or utc_now()
        job.heartbeat_at = utc_now()
        job.finished_at = None
        job.project.status = "processing"
        job.project.stage = "worker"
        job.project.message = f"Analysis attempt {job.attempt_count} is running."
        db.commit()
        return job.id
    finally:
        db.close()


def _heartbeat(job_id: int, stop_event: threading.Event) -> None:
    while not stop_event.wait(settings.INGESTION_HEARTBEAT_SECONDS):
        db = SessionLocal()
        try:
            job = db.get(IngestionJobModel, job_id)
            if not job or job.status != "running":
                return
            job.heartbeat_at = utc_now()
            db.commit()
        except Exception as exc:
            db.rollback()
            log_event("ingestion_heartbeat_failed", job_id=job_id, error_type=type(exc).__name__)
        finally:
            db.close()


def _run_pipeline(user_id: str, project_slug: str) -> None:
    if storage_enabled():
        db = SessionLocal()
        try:
            materialize_project_from_db(db, user_id, project_slug)
        finally:
            db.close()
    state = {
        "user_id": user_id,
        "project_name": project_slug,
        "project_path": str(project_root(user_id, project_slug)),
        "unprocessed_files": [],
        "processed_files": [],
        "comment_report": [],
        "scanned_files": 0,
        "errors": [],
    }
    recursion_limit = max(50, settings.MAX_PROJECT_FILES + 10)
    result = codegraph_app.invoke(state, config={"recursion_limit": recursion_limit})
    if result.get("errors") or read_status(user_id, project_slug).get("status") == "error":
        raise RuntimeError("Pipeline reported an ingestion error.")
    persist_project_snapshot(user_id, project_slug)


def execute_job(job_id: int) -> None:
    db = SessionLocal()
    job = db.get(IngestionJobModel, job_id)
    if not job or job.status != "running":
        db.close()
        return
    user_id, project_slug = job.user_id, job.project_slug
    db.close()

    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(target=_heartbeat, args=(job_id, heartbeat_stop), daemon=True)
    heartbeat_thread.start()
    try:
        _run_pipeline(user_id, project_slug)
        db = SessionLocal()
        try:
            job = db.get(IngestionJobModel, job_id)
            job.status = "completed"
            job.heartbeat_at = utc_now()
            job.finished_at = utc_now()
            job.error_code = None
            job.error_message = None
            job.project.status = "ready"
            job.project.stage = "complete"
            job.project.message = "Project is ready for chat."
            job.project.progress = 100
            job.project.error = None
            db.commit()
        finally:
            db.close()
        write_status(user_id, project_slug, "ready", "complete", "Project is ready for chat.", 100)
        log_event("ingestion_job_completed", job_id=job_id, user_id=user_id, project_name=project_slug)
    except Exception as exc:
        retryable = False
        db = SessionLocal()
        try:
            job = db.get(IngestionJobModel, job_id)
            if not job:
                raise RuntimeError("Ingestion job disappeared while handling a failure.") from exc
            retryable = job.attempt_count < job.max_attempts
            job.status = "retryable" if retryable else "failed"
            job.heartbeat_at = utc_now()
            job.finished_at = None if retryable else utc_now()
            job.error_code = type(exc).__name__
            job.error_message = str(exc)[:500]
            job.project.status = "retryable" if retryable else "error"
            job.project.stage = "worker"
            job.project.message = "Ingestion will be retried." if retryable else "Ingestion failed."
            job.project.error = job.error_code
            db.commit()
        finally:
            db.close()
        write_status(
            user_id,
            project_slug,
            "retryable" if retryable else "error",
            "worker",
            "Ingestion will be retried." if retryable else "Ingestion failed.",
            8,
            type(exc).__name__,
        )
        log_event(
            "ingestion_job_failed",
            job_id=job_id,
            user_id=user_id,
            project_name=project_slug,
            retryable=retryable,
            error_type=type(exc).__name__,
        )
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)


def worker_loop(stop_event: threading.Event) -> None:
    log_event("ingestion_worker_started")
    while not stop_event.is_set():
        try:
            job_id = claim_next_job()
            if job_id is None:
                stop_event.wait(settings.INGESTION_WORKER_POLL_SECONDS)
                continue
            execute_job(job_id)
        except Exception as exc:
            log_event("ingestion_worker_error", error_type=type(exc).__name__)
            stop_event.wait(settings.INGESTION_WORKER_POLL_SECONDS)
    log_event("ingestion_worker_stopped")


def job_payload(job: IngestionJobModel) -> dict:
    return {
        "id": job.id,
        "project_name": job.project_slug,
        "status": job.status,
        "attempt_count": job.attempt_count,
        "max_attempts": job.max_attempts,
        "heartbeat_at": job.heartbeat_at.isoformat() if job.heartbeat_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error_code": job.error_code,
        "error_message": job.error_message if job.status == "failed" else None,
    }


def require_user_job(db: Session, job_id: int, user_id: int | str) -> IngestionJobModel:
    job = db.query(IngestionJobModel).filter(
        IngestionJobModel.id == job_id,
        IngestionJobModel.user_id == str(user_id),
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Ingestion job not found.")
    return job


def retry_failed_job(db: Session, job_id: int, user_id: int | str) -> IngestionJobModel:
    job = require_user_job(db, job_id, user_id)
    if job.status not in {"failed", "retryable"}:
        raise HTTPException(status_code=409, detail="Only failed jobs can be retried.")
    job.status = "queued"
    job.attempt_count = 0
    job.finished_at = None
    job.error_code = None
    job.error_message = None
    job.project.status = "queued"
    job.project.stage = "queued"
    job.project.message = "Project is queued for another ingestion attempt."
    job.project.error = None
    db.commit()
    db.refresh(job)
    return job
