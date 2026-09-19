import os
from collections import Counter
from pathlib import Path

from config import (
    IGNORE_DIRS,
    IGNORE_EXTS,
    IGNORE_FILENAMES,
    SUPPORTED_SOURCE_EXTS,
    SUPPORTED_SOURCE_FILENAMES,
    settings,
)
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
    selected_bytes = 0
    skipped = Counter()

    for root, dirs, files in os.walk(project_path, followlinks=False):
        filtered_dirs = []
        for directory in dirs:
            directory_path = Path(root) / directory
            if directory.lower() in IGNORE_DIRS:
                skipped["ignored_directory"] += 1
            elif directory_path.is_symlink():
                skipped["symbolic_link"] += 1
            else:
                filtered_dirs.append(directory)
        dirs[:] = sorted(filtered_dirs)
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
            lowered_name = filename.lower()
            path = Path(full_path)
            if path.is_symlink():
                skipped["symbolic_link"] += 1
                continue
            if lowered_name in IGNORE_FILENAMES or lowered_name.startswith(".env."):
                skipped["sensitive_filename"] += 1
                continue
            if ext in IGNORE_EXTS:
                skipped["ignored_extension"] += 1
                continue
            if ext not in SUPPORTED_SOURCE_EXTS and lowered_name not in SUPPORTED_SOURCE_FILENAMES:
                skipped["unsupported_type"] += 1
                continue
            try:
                file_size = path.stat().st_size
            except OSError:
                skipped["unreadable"] += 1
                continue
            if file_size > settings.MAX_FILE_BYTES:
                skipped["file_too_large"] += 1
                continue
            if selected_bytes + file_size > settings.MAX_PROJECT_SOURCE_BYTES:
                skipped["project_byte_limit"] += 1
                continue
            tree_lines.append(f"{subindent}- {filename}")
            code_files.append(full_path)
            selected_bytes += file_size
            if len(code_files) >= settings.MAX_PROJECT_FILES:
                skipped["project_file_limit"] += 1
                dirs.clear()
                break
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
        "selected_bytes": selected_bytes,
        "skipped_files": sum(skipped.values()),
        "skip_reasons": dict(sorted(skipped.items())),
    }
