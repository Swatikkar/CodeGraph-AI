import gc
import os
import re
import time
from pathlib import Path

import numpy as np
from langchain_chroma import Chroma
from langchain_core.tools import tool

from config import IGNORE_DIRS, settings
from providers import model_router
from utils.chroma import ensure_chroma_defaults
from utils.secrets import redact_secrets
from utils.storage import project_root


_vectorstore_cache: dict[str, Chroma] = {}
_query_cache: dict[str, list] = {}
CACHE_THRESHOLD = 0.92
CACHE_MAX_SIZE = 100
CACHE_TTL_SECONDS = 3600


def get_vectorstore(project_name: str) -> Chroma:
    if project_name not in _vectorstore_cache:
        project_db_path = settings.CHROMA_DB_DIR / project_name
        client = ensure_chroma_defaults(project_db_path)
        _vectorstore_cache[project_name] = Chroma(
            persist_directory=str(project_db_path),
            embedding_function=model_router.embeddings(),
            client=client,
        )
    return _vectorstore_cache[project_name]


def close_vectorstore(vectorstore: Chroma | None):
    if vectorstore is None:
        return
    if os.name != "nt":
        gc.collect()
        return
    client = getattr(vectorstore, "_client", None)
    system = getattr(client, "_system", None)
    stop = getattr(system, "stop", None)
    if callable(stop):
        stop()
    gc.collect()


def invalidate_vectorstore_cache(project_name: str):
    close_vectorstore(_vectorstore_cache.pop(project_name, None))
    _query_cache.pop(project_name, None)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def check_semantic_cache(query_embedding: np.ndarray, project_name: str) -> str | None:
    if project_name not in _query_cache:
        return None
    now = time.time()
    cache = [entry for entry in _query_cache[project_name] if now - entry["timestamp"] < CACHE_TTL_SECONDS]
    _query_cache[project_name] = cache
    best_score = 0.0
    best_result = None
    for entry in cache:
        score = cosine_similarity(query_embedding, entry["embedding"])
        if score > best_score:
            best_score = score
            best_result = entry["result"]
    return best_result if best_score >= CACHE_THRESHOLD else None


def store_in_cache(query_embedding: np.ndarray, result: str, project_name: str):
    cache = _query_cache.setdefault(project_name, [])
    if len(cache) >= CACHE_MAX_SIZE:
        cache.pop(0)
    cache.append({"embedding": query_embedding, "result": result, "timestamp": time.time()})


def clean_tokens(text: str) -> set[str]:
    return set(re.findall(r"\b\w+\b", text.lower()))


def hybrid_retrieve(query: str, project_name: str, top_n: int = 5) -> str:
    vectorstore = get_vectorstore(project_name)
    raw_results = vectorstore.similarity_search(query, k=15)
    if not raw_results:
        return "No relevant code found in the database."

    query_tokens = clean_tokens(query)
    reranked_docs = []
    for doc in raw_results:
        content = doc.page_content.lower()
        filename = doc.metadata.get("filename", "").lower()
        relative_path = doc.metadata.get("relative_path", "").lower()
        exact_matches = sum(1 for token in query_tokens if token in content)
        filename_boost = 5 if any(token in filename or token in relative_path for token in query_tokens) else 0
        reranked_docs.append((exact_matches + filename_boost, doc))

    reranked_docs.sort(key=lambda item: item[0], reverse=True)
    context = []
    for idx, (_score, doc) in enumerate(reranked_docs[:top_n], start=1):
        source = doc.metadata.get("relative_path") or doc.metadata.get("source", "Unknown File")
        language = doc.metadata.get("language", "code")
        context.append(f"\n--- Context Block {idx} (File: {source}, Language: {language}) ---\n{redact_secrets(doc.page_content)}\n")
    return "\n".join(context)


def filesystem_retrieve(query: str, project_name: str, top_n: int = 5) -> str:
    try:
        user_id, slug = project_name.split("_", 1)
    except ValueError:
        return "Error: Project namespace is invalid."

    root = project_root(user_id, slug)
    if not root.exists():
        return "Error: Project files are not available."

    query_tokens = clean_tokens(query)
    scored_files = []
    allowed_suffixes = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rb", ".rs", ".php", ".cs"}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        lower_content = content.lower()
        rel_path = path.relative_to(root).as_posix()
        score = sum(1 for token in query_tokens if token in lower_content or token in rel_path.lower())
        if score:
            scored_files.append((score, rel_path, content))

    scored_files.sort(key=lambda item: item[0], reverse=True)
    if not scored_files:
        return "No relevant code found in project files."

    context = []
    for idx, (_score, rel_path, content) in enumerate(scored_files[:top_n], start=1):
        context.append(
            f"\n--- Context Block {idx} (File: {rel_path}, Language: {Path(rel_path).suffix.lower().lstrip('.') or 'code'}) ---\n"
            f"{redact_secrets(content[:settings.MAX_FILE_BYTES])}\n"
        )
    return "\n".join(context)


@tool
def retrieve_code_context(query: str, project_name: str, top_n: int = 5) -> str:
    """
    Search the user's ingested codebase with semantic retrieval plus keyword reranking.
    project_name must be the canonical isolated namespace, e.g. userId_projectSlug.
    """
    project_db_path = Path(settings.CHROMA_DB_DIR) / project_name
    if not project_db_path.exists():
        return "Error: Vector database not found. Has this project been ingested?"

    try:
        embeddings = model_router.embeddings()
        query_embedding = np.array(embeddings.embed_query(query))
        cached = check_semantic_cache(query_embedding, project_name)
        if cached is not None:
            return cached

        result = hybrid_retrieve(query, project_name, top_n)
        store_in_cache(query_embedding, result, project_name)
        return result
    except Exception as exc:
        print(f"[retrieval] Chroma retrieval unavailable, using filesystem fallback: {exc}", flush=True)
        return filesystem_retrieve(query, project_name, top_n)
