import gc
import hashlib
import math
import os
import re
import time
from pathlib import Path

import numpy as np
from langchain_chroma import Chroma
from langchain_core.tools import tool
from langchain_core.documents import Document

from config import IGNORE_DIRS, settings
from providers import model_router
from utils.chroma import VECTOR_COLLECTION_NAME, ensure_chroma_defaults
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
            collection_name=VECTOR_COLLECTION_NAME,
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


def tokenize(text: str) -> list[str]:
    identifiers = re.findall(r"[a-zA-Z][a-zA-Z0-9_]+", text)
    tokens: list[str] = []
    for identifier in identifiers:
        tokens.append(identifier.lower())
        expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", identifier.replace("_", " "))
        tokens.extend(re.findall(r"[a-zA-Z][a-zA-Z0-9]+", expanded.lower()))
    return tokens


def clean_tokens(text: str) -> set[str]:
    return set(tokenize(text))


def _doc_key(doc: Document) -> str:
    digest = hashlib.sha256(doc.page_content.encode("utf-8")).hexdigest()
    return f"{doc.metadata.get('relative_path', doc.metadata.get('source', ''))}:{digest}"


def _all_index_documents(vectorstore: Chroma) -> list[Document]:
    payload = vectorstore._collection.get(
        include=["documents", "metadatas"],
        limit=settings.RETRIEVAL_MAX_INDEX_DOCUMENTS,
    )
    documents = payload.get("documents") or []
    metadatas = payload.get("metadatas") or [{} for _ in documents]
    return [Document(page_content=content or "", metadata=metadata or {}) for content, metadata in zip(documents, metadatas)]


def _bm25_rank(query: str, documents: list[Document]) -> list[Document]:
    query_terms = clean_tokens(query)
    if not query_terms or not documents:
        return []
    tokenized = [tokenize(doc.page_content) for doc in documents]
    average_length = sum(len(tokens) for tokens in tokenized) / max(1, len(tokenized))
    document_frequency = {
        term: sum(1 for tokens in tokenized if term in set(tokens))
        for term in query_terms
    }
    scored: list[tuple[float, Document]] = []
    for doc, tokens in zip(documents, tokenized):
        frequencies = {term: tokens.count(term) for term in query_terms}
        length = len(tokens)
        score = 0.0
        for term in query_terms:
            frequency = frequencies[term]
            if not frequency:
                continue
            frequency_docs = document_frequency[term]
            inverse_frequency = math.log(1 + (len(documents) - frequency_docs + 0.5) / (frequency_docs + 0.5))
            denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * length / max(1.0, average_length))
            score += inverse_frequency * (frequency * 2.5 / denominator)
        path = str(doc.metadata.get("relative_path", "")).lower()
        symbols = clean_tokens(str(doc.metadata.get("symbols", "")))
        score += 4.0 * sum(1 for term in query_terms if term in symbols)
        score += 1.5 * sum(1 for term in query_terms if term in path)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [doc for _, doc in scored[: settings.RETRIEVAL_LEXICAL_CANDIDATES]]


def hybrid_retrieve(query: str, project_name: str, top_n: int = 5) -> str:
    vectorstore = get_vectorstore(project_name)
    vector_results = vectorstore.similarity_search(query, k=settings.RETRIEVAL_VECTOR_CANDIDATES)
    lexical_results = _bm25_rank(query, _all_index_documents(vectorstore))
    if not vector_results and not lexical_results:
        return "No relevant code found in the database."

    query_tokens = clean_tokens(query)
    documents: dict[str, Document] = {}
    scores: dict[str, float] = {}
    for rank, doc in enumerate(vector_results, start=1):
        key = _doc_key(doc)
        documents[key] = doc
        scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)
    for rank, doc in enumerate(lexical_results, start=1):
        key = _doc_key(doc)
        documents[key] = doc
        scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)
        symbol_tokens = clean_tokens(str(doc.metadata.get("symbols", "")))
        if query_tokens.intersection(symbol_tokens):
            scores[key] += 0.05
    reranked_docs = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    max_score = reranked_docs[0][1] if reranked_docs else 1.0
    context = []
    for idx, (key, score) in enumerate(reranked_docs[:top_n], start=1):
        doc = documents[key]
        source = doc.metadata.get("relative_path") or doc.metadata.get("source", "Unknown File")
        language = doc.metadata.get("language", "code")
        confidence = min(1.0, score / max_score)
        context.append(
            f"\n--- Context Block {idx} (File: {source}, Language: {language}, Confidence: {confidence:.3f}) ---\n"
            f"{redact_secrets(doc.page_content)}\n"
        )
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
