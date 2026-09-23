import hashlib
import json
import os
import tempfile
import tomllib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from config import settings
from models.user import PatchProposalModel, ProjectModel
from utils.guardrails import assert_supported_code_file
from utils.storage import resolve_project_file


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def content_checksum(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def validate_patch_syntax(path: Path, content: str) -> None:
    suffix = path.suffix.lower()
    try:
        if suffix == ".py":
            compile(content, str(path), "exec")
        elif suffix == ".json":
            json.loads(content)
        elif suffix == ".toml":
            tomllib.loads(content)
    except (SyntaxError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"Proposed {suffix or 'file'} content is invalid.") from exc


def create_patch_proposal(
    db: Session,
    user_id: int | str,
    project_slug: str,
    file_path: str,
    proposed_content: str,
) -> PatchProposalModel:
    project = db.query(ProjectModel).filter(
        ProjectModel.user_id == str(user_id),
        ProjectModel.slug == project_slug,
        ProjectModel.status == "ready",
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Ready project not found.")
    target = resolve_project_file(user_id, project_slug, file_path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Patch target not found.")
    assert_supported_code_file(target)
    if len(proposed_content.encode("utf-8")) > settings.MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Proposed file content exceeds the size limit.")
    validate_patch_syntax(target, proposed_content)
    current_content = target.read_text(encoding="utf-8")
    proposal = PatchProposalModel(
        id=uuid.uuid4().hex,
        project_id=project.id,
        user_id=str(user_id),
        file_path=file_path,
        base_checksum=content_checksum(current_content),
        proposed_content=proposed_content,
        status="pending",
        expires_at=utc_now() + timedelta(minutes=settings.PATCH_PROPOSAL_EXPIRE_MINUTES),
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    return proposal


def require_user_proposal(db: Session, proposal_id: str, user_id: int | str, project_id: int) -> PatchProposalModel:
    proposal = db.query(PatchProposalModel).filter(
        PatchProposalModel.id == proposal_id,
        PatchProposalModel.user_id == str(user_id),
        PatchProposalModel.project_id == project_id,
    ).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Patch proposal not found.")
    return proposal


def proposal_payload(proposal: PatchProposalModel) -> dict:
    return {
        "proposal_id": proposal.id,
        "file_path": proposal.file_path,
        "new_content": proposal.proposed_content,
        "status": proposal.status,
        "expires_at": proposal.expires_at.isoformat() if proposal.expires_at else None,
    }


def apply_patch_proposal(db: Session, proposal: PatchProposalModel) -> str:
    if proposal.status != "pending":
        raise HTTPException(status_code=409, detail="Patch proposal is no longer pending.")
    expires_at = proposal.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and expires_at <= utc_now():
        proposal.status = "expired"
        db.commit()
        raise HTTPException(status_code=409, detail="Patch proposal expired. Generate a new proposal.")

    target = resolve_project_file(proposal.user_id, proposal.project.slug, proposal.file_path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=409, detail="Patch target no longer exists.")
    assert_supported_code_file(target)
    current_content = target.read_text(encoding="utf-8")
    if content_checksum(current_content) != proposal.base_checksum:
        proposal.status = "stale"
        db.commit()
        raise HTTPException(status_code=409, detail="File changed after this proposal was created. Generate a fresh patch.")
    validate_patch_syntax(target, proposal.proposed_content)

    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
            handle.write(proposal.proposed_content)
            temporary_path = Path(handle.name)
        os.replace(temporary_path, target)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()

    proposal.status = "applied"
    proposal.applied_at = utc_now()
    db.commit()
    return f"Success: File '{proposal.file_path}' was updated from approved proposal {proposal.id}."
