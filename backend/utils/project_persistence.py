import hashlib
import json
import shutil
from pathlib import Path

from fastapi import HTTPException
from langchain_core.documents import Document
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import IGNORE_DIRS, IGNORE_EXTS, settings
from models.user import (
    ChatMessageModel,
    ProjectArtifactModel,
    ProjectChunkModel,
    ProjectFileModel,
    ProjectModel,
)
from tools.ingester import get_splitter_for_file, ingest_to_chroma
from utils.database import SessionLocal
from utils.secrets import redact_secrets
from utils.storage import (
    architecture_path,
    dependency_graph_path,
    ensure_project_dirs,
    ingestion_report_path,
    project_chroma_root,
    project_namespace,
    project_root,
    slugify_project_name,
    status_path,
    tree_cache_path,
)


ARTIFACT_PATHS = {
    "status": status_path,
    "tree": tree_cache_path,
    "architecture": architecture_path,
    "dependency_graph": dependency_graph_path,
    "ingestion_report": ingestion_report_path,
}


def storage_enabled() -> bool:
    return settings.use_supabase_storage


def normalize_user_id(user_id: int | str) -> str:
    return str(user_id)


def get_project(db: Session, user_id: int | str, slug: str) -> ProjectModel | None:
    return (
        db.query(ProjectModel)
        .filter(ProjectModel.user_id == normalize_user_id(user_id), ProjectModel.slug == slugify_project_name(slug))
        .first()
    )


def require_project(db: Session, user_id: int | str, slug: str) -> ProjectModel:
    project = get_project(db, user_id, slug)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


def reserve_project_ingestion(
    db: Session,
    user_id: int | str,
    slug: str,
    display_name: str,
    source_type: str,
    source_url: str | None = None,
    stage: str = "created",
    message: str | None = None,
) -> ProjectModel:
    """Atomically reject an active same-slug ingestion and reserve terminal projects for replacement."""
    normalized_user_id = normalize_user_id(user_id)
    slug = slugify_project_name(slug)
    project = (
        db.query(ProjectModel)
        .filter(ProjectModel.user_id == normalized_user_id, ProjectModel.slug == slug)
        .with_for_update()
        .first()
    )
    if project and project.status not in {"ready", "error"}:
        raise HTTPException(status_code=409, detail="This project is already being ingested.")

    if not project:
        project = ProjectModel(
            user_id=normalized_user_id,
            slug=slug,
            display_name=display_name,
            source_type=source_type,
        )
        db.add(project)

    project.display_name = display_name
    project.source_type = source_type
    project.source_url = source_url
    project.status = "processing"
    project.stage = stage
    project.message = message
    project.progress = 1
    project.error = None
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = get_project(db, normalized_user_id, slug)
        if existing:
            raise HTTPException(status_code=409, detail="This project is already being ingested.") from exc
        raise
    db.refresh(project)
    return project


def update_project_status(
    db: Session,
    user_id: int | str,
    slug: str,
    status: str,
    stage: str,
    message: str = "",
    progress: int = 0,
    error: str | None = None,
) -> None:
    project = get_project(db, user_id, slug)
    if not project:
        return
    project.status = status
    project.stage = stage
    project.message = message
    project.progress = progress
    project.error = error
    db.commit()


def sync_project_status(user_id: int | str, slug: str, status: str, stage: str, message: str = "", progress: int = 0, error: str | None = None) -> None:
    if not storage_enabled():
        return
    db = SessionLocal()
    try:
        update_project_status(db, user_id, slug, status, stage, message, progress, error)
    finally:
        db.close()


def iter_persistable_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        parts = set(rel.parts)
        if parts.intersection(IGNORE_DIRS):
            continue
        if path.suffix.lower() in IGNORE_EXTS:
            continue
        if path.stat().st_size > settings.MAX_FILE_BYTES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        yield rel.as_posix(), content


def upsert_project_file(db: Session, project: ProjectModel, path: str, content: str) -> None:
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    record = (
        db.query(ProjectFileModel)
        .filter(ProjectFileModel.project_id == project.id, ProjectFileModel.path == path)
        .first()
    )
    if not record:
        record = ProjectFileModel(project_id=project.id, user_id=project.user_id, path=path)
        db.add(record)
    record.content = content
    record.size_bytes = len(content.encode("utf-8"))
    record.checksum = checksum


def upsert_artifact(db: Session, project: ProjectModel, artifact_type: str, content: str) -> None:
    record = (
        db.query(ProjectArtifactModel)
        .filter(ProjectArtifactModel.project_id == project.id, ProjectArtifactModel.artifact_type == artifact_type)
        .first()
    )
    if not record:
        record = ProjectArtifactModel(project_id=project.id, user_id=project.user_id, artifact_type=artifact_type)
        db.add(record)
    record.content = content


def replace_project_chunks(db: Session, project: ProjectModel, files: list[tuple[str, str]]) -> int:
    db.query(ProjectChunkModel).filter(ProjectChunkModel.project_id == project.id).delete()
    chunk_count = 0
    for rel_path, content in files:
        doc = Document(page_content=redact_secrets(content), metadata={"relative_path": rel_path})
        splitter = get_splitter_for_file(rel_path)
        for idx, chunk in enumerate(splitter.split_documents([doc])):
            chunk_count += 1
            db.add(
                ProjectChunkModel(
                    project_id=project.id,
                    user_id=project.user_id,
                    path=rel_path,
                    chunk_index=idx,
                    content=chunk.page_content,
                    embedding=None,
                )
            )
    return chunk_count


def persist_project_snapshot(user_id: int | str, slug: str) -> None:
    if not storage_enabled():
        return

    db = SessionLocal()
    try:
        print(f"[persistence] snapshot start user={user_id} slug={slug}", flush=True)
        project = require_project(db, user_id, slug)
        root = project_root(user_id, slug)
        if not root.exists():
            print(f"[persistence] snapshot skipped missing root user={user_id} slug={slug}", flush=True)
            return

        files = list(iter_persistable_files(root))
        db.query(ProjectFileModel).filter(ProjectFileModel.project_id == project.id).delete()
        for rel_path, content in files:
            upsert_project_file(db, project, rel_path, content)

        db.query(ProjectArtifactModel).filter(ProjectArtifactModel.project_id == project.id).delete()
        for artifact_type, path_factory in ARTIFACT_PATHS.items():
            path = path_factory(user_id, slug)
            if path.exists() and path.is_file():
                upsert_artifact(db, project, artifact_type, path.read_text(encoding="utf-8", errors="replace"))

        artifact_count = db.query(ProjectArtifactModel).filter(ProjectArtifactModel.project_id == project.id).count()
        chunk_count = replace_project_chunks(db, project, files)
        db.commit()
        print(
            f"[persistence] snapshot complete user={user_id} slug={slug} "
            f"files={len(files)} artifacts={artifact_count} chunks={chunk_count}",
            flush=True,
        )
    finally:
        db.close()


def materialize_project_from_db(db: Session, user_id: int | str, slug: str) -> ProjectModel:
    project = require_project(db, user_id, slug)
    ensure_project_dirs(user_id, slug)
    root = project_root(user_id, slug)
    root.mkdir(parents=True, exist_ok=True)

    for record in db.query(ProjectFileModel).filter(ProjectFileModel.project_id == project.id).all():
        target = root / record.path
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_text(encoding="utf-8", errors="replace") != record.content:
            target.write_text(record.content, encoding="utf-8")

    for artifact in db.query(ProjectArtifactModel).filter(ProjectArtifactModel.project_id == project.id).all():
        path_factory = ARTIFACT_PATHS.get(artifact.artifact_type)
        if path_factory:
            target = path_factory(user_id, slug)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(artifact.content, encoding="utf-8")

    return project


def ensure_retrieval_cache(db: Session, user_id: int | str, slug: str) -> None:
    if not storage_enabled():
        return
    namespace = project_namespace(user_id, slug)
    chroma_root = project_chroma_root(user_id, slug)
    if chroma_root.exists():
        return
    materialize_project_from_db(db, user_id, slug)
    files = [str(project_root(user_id, slug) / record.path) for record in db.query(ProjectFileModel).filter(ProjectFileModel.project_id == require_project(db, user_id, slug).id).all()]
    if files:
        ingest_to_chroma(files, namespace, project_root=str(project_root(user_id, slug)), user_id=user_id)


def project_status_payload(project: ProjectModel) -> dict:
    return {
        "status": project.status,
        "stage": project.stage,
        "message": project.message or "",
        "progress": project.progress,
        "error": project.error,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


def list_user_projects_from_db(db: Session, user_id: int | str) -> list[dict]:
    projects = (
        db.query(ProjectModel)
        .filter(ProjectModel.user_id == normalize_user_id(user_id), ProjectModel.status != "deleted")
        .order_by(ProjectModel.updated_at.desc())
        .all()
    )
    return [{"name": project.slug, **project_status_payload(project)} for project in projects if project.status != "delete_pending"]


def persist_chat_message(db: Session, user_id: int | str, slug: str, role: str, content: str) -> None:
    if not storage_enabled() or not content.strip():
        return
    project = require_project(db, user_id, slug)
    db.add(ChatMessageModel(project_id=project.id, user_id=project.user_id, role=role, content=content))
    db.commit()


def read_chat_messages(db: Session, user_id: int | str, slug: str) -> list[dict]:
    project = require_project(db, user_id, slug)
    messages = (
        db.query(ChatMessageModel)
        .filter(ChatMessageModel.project_id == project.id, ChatMessageModel.user_id == project.user_id)
        .order_by(ChatMessageModel.created_at.asc(), ChatMessageModel.id.asc())
        .all()
    )
    return [{"role": message.role, "content": message.content} for message in messages]


def delete_project_from_db(db: Session, user_id: int | str, slug: str) -> bool:
    project = require_project(db, user_id, slug)
    project_id = project.id
    db.query(ChatMessageModel).filter(ChatMessageModel.project_id == project_id, ChatMessageModel.user_id == project.user_id).delete()
    db.query(ProjectChunkModel).filter(ProjectChunkModel.project_id == project_id, ProjectChunkModel.user_id == project.user_id).delete()
    db.query(ProjectArtifactModel).filter(ProjectArtifactModel.project_id == project_id, ProjectArtifactModel.user_id == project.user_id).delete()
    db.query(ProjectFileModel).filter(ProjectFileModel.project_id == project_id, ProjectFileModel.user_id == project.user_id).delete()
    db.delete(project)
    db.commit()
    return True
