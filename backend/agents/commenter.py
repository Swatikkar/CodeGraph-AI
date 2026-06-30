import os

from providers import model_router
from tools.ast_parser import EXTENSION_MAP, parse_code_file
from utils.secrets import contains_secret, redact_secrets


CHUNK_SYSTEM = """You are an expert Senior {language} Developer. Add one clear documentation comment explaining the purpose of the given class or function.

Rules:
1. Use this exact comment style: {comment_style}
2. Insert the comment in the most idiomatic location for {language}.
3. Do not add conversational text.
4. Output only valid code.
5. Preserve indentation and original behavior.
"""

FILE_SYSTEM = """You are an expert Senior {language} Developer. Write one brief top-level summary comment explaining this file's purpose.

Rules:
1. Use this exact comment style: {comment_style}
2. Keep it to 1-3 sentences.
3. Do not output markdown or conversational text.
"""


def _strip_fences(text: str, language: str) -> str:
    return (text or "").replace(f"```{language}", "").replace("```", "").strip()


def generate_chunk_comments(code_chunk: str, language: str, comment_style: str) -> str:
    if contains_secret(code_chunk):
        return code_chunk
    try:
        result, _metadata = model_router.invoke_text(
            "commenter_code_docs",
            CHUNK_SYSTEM.format(language=language.capitalize(), comment_style=comment_style),
            f"Here is the code chunk to comment:\n\n{redact_secrets(code_chunk)}",
        )
        if _metadata.get("provider") == "none":
            return code_chunk
        return _strip_fences(result, language) or code_chunk
    except Exception as exc:
        print(f"LLM Error (Chunk): {exc}")
        return code_chunk


def generate_file_summary(file_content: str, language: str, comment_style: str) -> str:
    try:
        result, _metadata = model_router.invoke_text(
            "commenter_code_docs",
            FILE_SYSTEM.format(language=language.capitalize(), comment_style=comment_style),
            f"Here is the file content to summarize:\n\n{redact_secrets(file_content) or '// Empty module file'}",
        )
        if _metadata.get("provider") == "none":
            return '"""\nFile summary unavailable because no model provider is configured.\n"""' if language == "python" else "/**\n * File summary unavailable because no model provider is configured.\n */"
        doc_comment = _strip_fences(result, language)
        if language == "python" and not doc_comment.startswith(('"""', "'''")):
            doc_comment = f'"""\n{doc_comment}\n"""'
        elif language in {"javascript", "java"} and not doc_comment.startswith("/**"):
            doc_comment = f"/**\n * {doc_comment.replace(chr(10), chr(10) + ' * ')}\n */"
        return doc_comment
    except Exception as exc:
        print(f"LLM Error (File Summary): {exc}")
        return '"""\nModule configuration file.\n"""' if language == "python" else "/**\n * Module configuration file.\n */"


def commenter_node(state: dict):
    unprocessed = state.get("unprocessed_files", [])
    processed = state.get("processed_files", [])
    comment_report = state.get("comment_report", [])

    if not unprocessed:
        return {"unprocessed_files": unprocessed, "processed_files": processed, "comment_report": comment_report}

    file_path = unprocessed.pop(0)
    ext = os.path.splitext(file_path)[1].lower()
    lang_profile = EXTENSION_MAP.get(ext, {
        "lang": "python",
        "style": 'Python triple-quote docstring structure ("""comment""")',
    })
    file_lang = lang_profile["lang"]
    comment_style = lang_profile["style"]

    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            file_content = handle.read()

        if not file_content.strip():
            processed.append(file_path)
            comment_report.append({"file": file_path, "status": "skipped_empty"})
            return {"unprocessed_files": unprocessed, "processed_files": processed, "comment_report": comment_report}

        chunks = parse_code_file(file_path)
        changes_made = False

        if chunks:
            for chunk in chunks:
                original_code = chunk["code"]
                commented_code = generate_chunk_comments(
                    original_code,
                    chunk.get("language", file_lang),
                    chunk.get("comment_style", comment_style),
                )
                if original_code in file_content and commented_code != original_code:
                    file_content = file_content.replace(original_code, commented_code, 1)
                    changes_made = True
        else:
            has_doc = file_content.strip().startswith(('"""', "'''", "/**", "/*"))
            if not has_doc:
                file_content = f"{generate_file_summary(file_content, file_lang, comment_style)}\n\n{file_content}"
                changes_made = True

        if changes_made:
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write(file_content)

        processed.append(file_path)
        comment_report.append({"file": file_path, "status": "commented" if changes_made else "unchanged"})
    except Exception as exc:
        comment_report.append({"file": file_path, "status": "error", "error": str(exc)})
        processed.append(file_path)

    return {
        "unprocessed_files": unprocessed,
        "processed_files": processed,
        "comment_report": comment_report,
    }
