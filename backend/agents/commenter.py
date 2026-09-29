import json
import re
from pathlib import Path

from config import settings
from providers import model_router
from tools.ast_parser import EXTENSION_MAP, parse_code_file
from utils.prompt_security import UNTRUSTED_CONTEXT_RULE, wrap_untrusted_context
from utils.secrets import contains_secret, redact_secrets


DOCUMENTATION_SYSTEM = """You are a senior {language} developer documenting source code for another developer.

Return one JSON object with this exact shape:
{{"file_summary":"one concise sentence","symbols":{{"symbol-key":"one concise sentence"}}}}

Rules:
1. Explain purpose and responsibility, not syntax.
2. Return one sentence for every supplied symbol key.
3. Do not return source code, markdown, or comment markers.
4. Do not suggest or make logic changes.
5. {untrusted_context_rule}
"""


COMMENT_STYLE_BY_EXTENSION = {
    ".py": "hash", ".rb": "hash", ".sh": "hash", ".bash": "hash",
    ".sql": "sql",
    ".html": "html", ".htm": "html", ".vue": "html", ".svelte": "html",
    ".js": "slash", ".jsx": "slash", ".ts": "slash", ".tsx": "slash",
    ".java": "slash", ".go": "slash", ".rs": "slash", ".c": "slash",
    ".h": "slash", ".cpp": "slash", ".hpp": "slash", ".cs": "slash",
    ".php": "slash", ".swift": "slash", ".kt": "slash", ".kts": "slash",
    ".scala": "slash", ".css": "slash", ".scss": "slash", ".sass": "slash",
}


def _clean_sentence(value: object, fallback: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().strip("`\"'")
    return (text or fallback)[:400]


def _format_comment(text: str, style: str, indent: str = "") -> str:
    if style == "hash":
        return f"{indent}# {text}"
    if style == "sql":
        return f"{indent}-- {text}"
    if style == "html":
        return f"{indent}<!-- {text} -->"
    return f"{indent}/** {text} */"


def _module_insert_index(lines: list[str], extension: str) -> int:
    if not lines:
        return 0
    if extension in {".html", ".htm"} and lines[0].lstrip().lower().startswith("<!doctype"):
        return 1
    if extension == ".php" and lines[0].lstrip().startswith("<?php"):
        return 1
    index = 1 if lines[0].startswith("#!") else 0
    if extension == ".py" and index < len(lines) and "coding" in lines[index][:40]:
        index += 1
    return index


def _documentation_payload(file_path: str, file_content: str, chunks: list[dict]) -> tuple[dict, dict]:
    extension = Path(file_path).suffix.lower()
    language = EXTENSION_MAP.get(extension, {}).get("lang", extension.lstrip(".") or "source")
    default_summary = f"This {language} file contains application source code and related logic."
    fallback_symbols = {
        f"{chunk['type']}:{chunk['name']}:{chunk['start_line']}":
        f"{chunk['type'].title()} {chunk['name']} contains the logic for this part of the application."
        for chunk in chunks
    }
    fallback = {"file_summary": default_summary, "symbols": fallback_symbols}

    if not settings.ENABLE_LLM_CODE_COMMENTING or contains_secret(file_content):
        return fallback, {"provider": "deterministic", "model": "documentation-fallback"}

    prompt_symbols = []
    remaining_chars = 16_000
    for chunk in chunks:
        key = f"{chunk['type']}:{chunk['name']}:{chunk['start_line']}"
        snippet = redact_secrets(chunk.get("code", ""))[: min(3_000, remaining_chars)]
        prompt_symbols.append({"key": key, "code": snippet})
        remaining_chars -= len(snippet)
        if remaining_chars <= 0:
            break

    prompt = wrap_untrusted_context(json.dumps({
        "file": Path(file_path).name,
        "language": language,
        "source_excerpt": redact_secrets(file_content)[:4_000] if not chunks else "",
        "symbols": prompt_symbols,
    }, ensure_ascii=False))
    try:
        raw, metadata = model_router.invoke_text(
            "commenter_code_docs",
            DOCUMENTATION_SYSTEM.format(
                language=language,
                untrusted_context_rule=UNTRUSTED_CONTEXT_RULE,
            ),
            prompt,
        )
        if metadata.get("provider") == "none":
            return fallback, {"provider": "deterministic", "model": "documentation-fallback"}
        clean = (raw or "").replace("```json", "").replace("```", "").strip()
        parsed = json.loads(clean)
        supplied_symbols = parsed.get("symbols") if isinstance(parsed, dict) else {}
        if not isinstance(supplied_symbols, dict):
            supplied_symbols = {}
        return {
            "file_summary": _clean_sentence(
                parsed.get("file_summary") if isinstance(parsed, dict) else "",
                default_summary,
            ),
            "symbols": {
                key: _clean_sentence(supplied_symbols.get(key), value)
                for key, value in fallback_symbols.items()
            },
        }, metadata
    except Exception:
        return fallback, {"provider": "deterministic", "model": "documentation-fallback"}


def _insert_documentation(file_content: str, extension: str, chunks: list[dict], documentation: dict) -> str:
    lines = file_content.splitlines()
    style = COMMENT_STYLE_BY_EXTENSION.get(extension, "slash")
    symbols = documentation.get("symbols", {})

    for chunk in sorted(chunks, key=lambda item: item["start_line"], reverse=True):
        line_index = max(0, min(len(lines), chunk["start_line"] - 1))
        if extension == ".py":
            while line_index > 0 and lines[line_index - 1].lstrip().startswith("@"):
                line_index -= 1
        source_line = lines[line_index] if line_index < len(lines) else ""
        indent = source_line[: len(source_line) - len(source_line.lstrip())]
        key = f"{chunk['type']}:{chunk['name']}:{chunk['start_line']}"
        fallback = f"{chunk['type'].title()} {chunk['name']} contains application logic."
        lines.insert(line_index, _format_comment(_clean_sentence(symbols.get(key), fallback), style, indent))

    summary = _clean_sentence(
        documentation.get("file_summary"),
        "This file contains application source code and related logic.",
    )
    lines.insert(_module_insert_index(lines, extension), _format_comment(summary, style))
    return "\n".join(lines) + ("\n" if file_content.endswith("\n") else "")


def commenter_node(state: dict):
    unprocessed = state.get("unprocessed_files", [])
    processed = state.get("processed_files", [])
    comment_report = state.get("comment_report", [])

    if not unprocessed:
        return {"unprocessed_files": unprocessed, "processed_files": processed, "comment_report": comment_report}

    file_path = unprocessed.pop(0)
    try:
        file_content = Path(file_path).read_text(encoding="utf-8")
        if not file_content.strip():
            status = "skipped_empty"
            metadata = {"provider": "none", "model": "none"}
        else:
            chunks = parse_code_file(file_path)
            documentation, metadata = _documentation_payload(file_path, file_content, chunks)
            Path(file_path).write_text(
                _insert_documentation(file_content, Path(file_path).suffix.lower(), chunks, documentation),
                encoding="utf-8",
            )
            status = "commented"
        processed.append(file_path)
        comment_report.append({
            "file": file_path,
            "status": status,
            "provider": metadata.get("provider", "unknown"),
            "model": metadata.get("model", "unknown"),
        })
    except Exception as exc:
        comment_report.append({"file": file_path, "status": "error", "error": type(exc).__name__})
        processed.append(file_path)

    return {
        "unprocessed_files": unprocessed,
        "processed_files": processed,
        "comment_report": comment_report,
    }
