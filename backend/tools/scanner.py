import os
from pathlib import Path

from config import IGNORE_DIRS, IGNORE_EXTS, settings
from utils.storage import tree_cache_path


def get_codebase_map(project_path: str, user_id: int | str | None = None, project_name: str | None = None) -> dict:
    """
    Scan a project, write a deterministic tree cache, and return processable files.
    Generated/vendor/large files are skipped before they reach parser/commenter/ingestion.
    """
    if not os.path.exists(project_path):
        raise FileNotFoundError(f"Project path not found: {project_path}")

    resolved_project_name = project_name or os.path.basename(project_path)
    tree_lines: list[str] = []
    code_files: list[str] = []

    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        level = root.replace(project_path, "").count(os.sep)
        indent = " " * 4 * level
        folder_name = os.path.basename(root)

        if level > 0 or folder_name:
            tree_lines.append(f"{indent}{folder_name}/")
            subindent = " " * 4 * (level + 1)
        else:
            subindent = ""

        for filename in sorted(files):
            ext = os.path.splitext(filename)[1].lower()
            full_path = os.path.join(root, filename)
            if ext in IGNORE_EXTS or os.path.getsize(full_path) > settings.MAX_FILE_BYTES:
                continue
            tree_lines.append(f"{subindent}- {filename}")
            code_files.append(full_path)
            if len(code_files) >= settings.MAX_PROJECT_FILES:
                break

    tree_string = "\n".join(tree_lines)
    if user_id is not None:
        output_path = tree_cache_path(user_id, resolved_project_name)
    else:
        output_path = Path(settings.PROJECT_TREES_DIR) / f"{resolved_project_name}.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(tree_string, encoding="utf-8")

    return {
        "tree": tree_string,
        "tree_cache_path": str(output_path),
        "files_to_process": code_files,
        "total_files": len(code_files),
    }
