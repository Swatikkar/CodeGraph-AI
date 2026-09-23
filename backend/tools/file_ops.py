from pathlib import Path

from langchain_core.tools import tool

from utils.guardrails import assert_supported_code_file
from utils.secrets import redact_secrets
from utils.storage import resolve_project_file, tree_cache_path
from utils.database import SessionLocal
from utils.patch_proposals import create_patch_proposal


def split_project_namespace(project_name: str) -> tuple[str, str]:
    if "_" not in project_name:
        raise ValueError("project_name must be the canonical namespace userId_projectSlug.")
    user_id, slug = project_name.split("_", 1)
    return user_id, slug


@tool
def get_project_tree(project_name: str) -> str:
    """
    Return the folder/file tree for the uploaded project.
    project_name must be the canonical namespace, e.g. userId_projectSlug.
    """
    try:
        user_id, slug = split_project_namespace(project_name)
        path = tree_cache_path(user_id, slug)
        if not path.exists():
            return f"Error: Project tree map for '{project_name}' not found."
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        return f"Failed to read project tree: {exc}"


@tool
def read_project_file(project_name: str, file_path: str) -> str:
    """
    Read a code file from the user's project. Use relative paths only.
    """
    try:
        user_id, slug = split_project_namespace(project_name)
        target = resolve_project_file(user_id, slug, file_path)
        if not target.exists():
            return f"Error: File '{file_path}' not found."
        assert_supported_code_file(target)
        return redact_secrets(target.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"Failed to read file: {exc}"


@tool
def propose_patch(project_name: str, file_path: str, new_content: str) -> str:
    """
    Propose a complete file replacement. This does not write to disk.
    The UI must ask the user for approval before the backend writes anything.
    """
    try:
        user_id, slug = split_project_namespace(project_name)
        db = SessionLocal()
        try:
            proposal = create_patch_proposal(db, user_id, slug, file_path, new_content)
        finally:
            db.close()
        return (
            "PATCH_PREVIEW_READY\n"
            f"proposal_id={proposal.id}\nfile={file_path}\n"
            "The proposed replacement content is ready for user approval."
        )
    except Exception as exc:
        return f"Failed to prepare patch: {exc}"


# Backwards-compatible names for existing imports.
read_code_file = read_project_file
write_code_file = propose_patch
