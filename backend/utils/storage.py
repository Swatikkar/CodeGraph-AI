import json
import re
import shutil
import os
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from config import settings


SLUG_PATTERN = re.compile(r"[^a-zA-Z0-9_.-]+")


def slugify_project_name(name: str) -> str:
    slug = SLUG_PATTERN.sub("-", name.strip()).strip(".-")
    if not slug:
        raise HTTPException(status_code=400, detail="Project name must contain letters or numbers.")
    return slug[:80]


def project_namespace(user_id: int | str, project_name: str) -> str:
    return f"{user_id}_{slugify_project_name(project_name)}"


def user_upload_root(user_id: int | str) -> Path:
    return settings.UPLOAD_DIR / str(user_id)


def project_root(user_id: int | str, project_name: str) -> Path:
    return user_upload_root(user_id) / slugify_project_name(project_name)


def project_artifact_root(user_id: int | str, project_name: str) -> Path:
    return settings.ARTIFACTS_DIR / str(user_id) / slugify_project_name(project_name)


def project_chroma_root(user_id: int | str, project_name: str) -> Path:
    return settings.CHROMA_DB_DIR / project_namespace(user_id, project_name)


def tree_cache_path(user_id: int | str, project_name: str) -> Path:
    return settings.PROJECT_TREES_DIR / f"{project_namespace(user_id, project_name)}.txt"


def status_path(user_id: int | str, project_name: str) -> Path:
    return project_artifact_root(user_id, project_name) / "status.json"


def architecture_path(user_id: int | str, project_name: str) -> Path:
    return project_artifact_root(user_id, project_name) / "architecture.mmd"


def dependency_graph_path(user_id: int | str, project_name: str) -> Path:
    return project_artifact_root(user_id, project_name) / "dependency_graph.mmd"


def ingestion_report_path(user_id: int | str, project_name: str) -> Path:
    return project_artifact_root(user_id, project_name) / "ingestion_report.json"


def ensure_project_dirs(user_id: int | str, project_name: str):
    user_upload_root(user_id).mkdir(parents=True, exist_ok=True)
    project_artifact_root(user_id, project_name).mkdir(parents=True, exist_ok=True)
    settings.PROJECT_TREES_DIR.mkdir(parents=True, exist_ok=True)
    settings.CHROMA_DB_DIR.mkdir(parents=True, exist_ok=True)


def resolve_project_file(user_id: int | str, project_name: str, relative_path: str) -> Path:
    root = project_root(user_id, project_name).resolve()
    target = (root / relative_path).resolve()
    try:
        if not target.is_relative_to(root):
            raise HTTPException(status_code=403, detail="Access denied.")
    except AttributeError:
        if str(root) not in str(target):
            raise HTTPException(status_code=403, detail="Access denied.")
    return target


def write_status(user_id: int | str, project_name: str, status: str, stage: str, message: str = "", progress: int = 0, error: str | None = None):
    ensure_project_dirs(user_id, project_name)
    payload = {
        "status": status,
        "stage": stage,
        "message": message,
        "progress": progress,
        "error": error,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = status_path(user_id, project_name)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            payload["created_at"] = existing.get("created_at") or payload["updated_at"]
        except Exception:
            payload["created_at"] = payload["updated_at"]
    else:
        payload["created_at"] = payload["updated_at"]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if settings.use_supabase_storage:
        try:
            from utils.project_persistence import sync_project_status

            sync_project_status(user_id, project_name, status, stage, message, progress, error)
        except Exception as exc:
            print(f"[storage] warning: failed to sync project status to database: {exc}", flush=True)
    return payload


def read_status(user_id: int | str, project_name: str) -> dict:
    path = status_path(user_id, project_name)
    if not path.exists():
        return {
            "status": "ready",
            "stage": "complete",
            "message": "Project is ready.",
            "progress": 100,
            "error": None,
        }
    return json.loads(path.read_text(encoding="utf-8"))


def delete_project_artifacts(user_id: int | str, project_name: str) -> bool:
    deletion_errors = []

    def remove_readonly(func, path, exc_info):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception as exc:
            deletion_errors.append(f"{path}: {exc}")

    def robust_rmtree(path: Path):
        last_error = None
        for attempt in range(1, 6):
            deletion_errors.clear()
            try:
                shutil.rmtree(path, onerror=remove_readonly)
                if not path.exists():
                    return True
            except Exception as exc:
                last_error = exc
            if not path.exists():
                return True
            time.sleep(0.3 * attempt)
        if path.exists():
            marker = path.with_name(f"{path.name}.delete_pending")
            try:
                path.rename(marker)
                return False
            except Exception as exc:
                last_error = exc
        if last_error or deletion_errors:
            print(
                f"[delete-project] deferred cleanup for {path}: "
                f"{last_error or '; '.join(deletion_errors[:3])}",
                flush=True,
            )
        return False

    targets = [
        project_root(user_id, project_name),
        project_artifact_root(user_id, project_name),
        tree_cache_path(user_id, project_name),
    ]
    fully_removed = True
    for target in targets:
        if target.is_dir():
            fully_removed = robust_rmtree(target) and fully_removed
        elif target.exists():
            try:
                target.unlink()
            except Exception as exc:
                fully_removed = False
                print(f"[delete-project] deferred cleanup for {target}: {exc}", flush=True)
    return fully_removed
