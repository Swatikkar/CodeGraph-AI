import ast
import os
import re
from pathlib import Path

from providers import model_router
from utils.storage import architecture_path, dependency_graph_path, ingestion_report_path


ARCHITECTURE_SYSTEM = """You are an expert software architect. Generate a high-level Mermaid flowchart explaining what this project does and how its major components interact.

Rules:
1. Start with exactly: graph TD
2. Do not output markdown fences.
3. Use concise quoted labels.
4. Show product/runtime concepts, not every file.
5. Include frontend, backend, data stores, external services, and agent/RAG flows when present.
"""


def extract_imports_from_file(file_path: str) -> list[str]:
    imports: list[str] = []
    ext = os.path.splitext(file_path)[1].lower()
    try:
        content = Path(file_path).read_text(encoding="utf-8")
        if ext == ".py":
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append(node.module)
        elif ext in [".js", ".jsx", ".ts", ".tsx"]:
            pattern = r"import\s+.*?from\s+['\"](.*?)['\"]|require\(['\"](.*?)['\"]\)"
            imports.extend(match[0] or match[1] for match in re.findall(pattern, content))
        elif ext == ".java":
            imports.extend(re.findall(r"import\s+(.*?);", content))
    except Exception:
        pass
    return sorted(set(i for i in imports if i))


def _safe_id(label: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]", "_", label)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "unknown"


def _node(label: str) -> str:
    return f'{_safe_id(label)}["{label.replace(chr(34), "")}"]'


def generate_dependency_graph(processed_files: list[str], project_root: str) -> tuple[str, dict[str, list[str]]]:
    dependency_map: dict[str, list[str]] = {}
    root = Path(project_root)
    lines = ["graph TD"]

    for file_path in processed_files:
        path = Path(file_path)
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = path.name
        imports = extract_imports_from_file(str(path))
        if imports:
            dependency_map[relative] = imports
            for imported in imports[:12]:
                lines.append(f"    {_node(relative)} --> {_node(imported)}")

    if len(lines) == 1:
        lines.append('    Project["Project"] --> Files["Source files"]')
    return "\n".join(lines), dependency_map


def sanitize_mermaid(raw: str) -> str:
    clean = (raw or "").replace("```mermaid", "").replace("```", "").strip()
    clean = re.sub(r"-->\|([^|]+)\|>", r"-->|\1|", clean)
    if not clean.startswith(("graph ", "flowchart ")):
        clean = "graph TD\n" + clean
    return clean


def architect_node(state: dict):
    processed_files = state.get("processed_files", [])
    project_path = state.get("project_path", "")
    user_id = state.get("user_id")
    project_name = state.get("project_name")

    if not processed_files or not project_path or user_id is None or not project_name:
        return state

    dependency_graph, dependency_map = generate_dependency_graph(processed_files, project_path)
    dependency_graph_path(user_id, project_name).write_text(dependency_graph, encoding="utf-8")

    summary_lines = []
    for file_path in processed_files[:80]:
        path = Path(file_path)
        try:
            relative = path.relative_to(project_path).as_posix()
        except Exception:
            relative = path.name
        imports = dependency_map.get(relative, [])
        summary_lines.append(f"- {relative}: imports {', '.join(imports[:8]) if imports else 'no major imports'}")

    try:
        raw, metadata = model_router.invoke_text(
            "architecture_design",
            ARCHITECTURE_SYSTEM,
            "Project file/import summary:\n" + "\n".join(summary_lines),
        )
        architecture = sanitize_mermaid(raw)
    except Exception:
        metadata = {"provider": "none", "model": "none"}
        architecture = "graph TD\n    User[\"User\"] --> App[\"Application\"]\n    App --> Code[\"Codebase\"]"

    architecture_path(user_id, project_name).write_text(architecture, encoding="utf-8")

    report = {
        "processed_files": len(processed_files),
        "dependency_edges": sum(len(v) for v in dependency_map.values()),
        "architecture_model": metadata,
        "comment_report": state.get("comment_report", []),
    }
    ingestion_report_path(user_id, project_name).write_text(__import__("json").dumps(report, indent=2), encoding="utf-8")
    return {**state, "dependency_map": dependency_map}
