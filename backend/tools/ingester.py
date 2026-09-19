# tools/ingester.py
import os
import shutil
import stat
import time
import gc
from pathlib import Path
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter, Language
from langchain_chroma import Chroma
from config import settings
from providers import model_router
from tools.retriever import close_vectorstore, invalidate_vectorstore_cache
from utils.chroma import VECTOR_COLLECTION_NAME, ensure_chroma_defaults
from utils.secrets import redact_secrets

# Helper to bypass Windows "Access Denied" errors when deleting SQLite files
def remove_readonly(func, path, _):
    """Clears the readonly bit and reattempts the removal."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


# FIX: Map file extensions to LangChain Language enum for proper syntax-aware splitting.
# Previously ALL files were split using Language.PYTHON regardless of their actual language.
EXTENSION_TO_LANGUAGE = {
    ".py":   Language.PYTHON,
    ".js":   Language.JS,
    ".jsx":  Language.JS,
    ".ts":   Language.TS,
    ".tsx":  Language.TS,
    ".go":   Language.GO,
    ".java": Language.JAVA,
    ".rb":   Language.RUBY,
    ".rs":   Language.RUST,
    ".cpp":  Language.CPP,
    ".c":    Language.C,
    ".cs":   Language.CSHARP,
    ".php":  Language.PHP,
}


def get_splitter_for_file(file_path: str) -> RecursiveCharacterTextSplitter:
    """
    Returns a language-aware text splitter based on the file extension.
    Falls back to a generic splitter for unknown file types.
    """
    ext = os.path.splitext(file_path)[1].lower()
    language = EXTENSION_TO_LANGUAGE.get(ext)

    if language:
        return RecursiveCharacterTextSplitter.from_language(
            language=language,
            chunk_size=1500,
            chunk_overlap=200
        )
    else:
        # Generic splitter for CSS, HTML, YAML, Markdown etc.
        return RecursiveCharacterTextSplitter(
            chunk_size=1500,
            chunk_overlap=200
        )


def ingest_to_chroma(
    files_to_process: list,
    project_name: str,
    project_root: str | None = None,
    user_id: int | str | None = None,
    replace_existing: bool = True,
):
    """
    Reads physical code files, chunks them using language-aware splitting,
    and embeds them into a Chroma vector store.
    """
    print(f"\nStarting vector ingestion for {project_name}...")

    all_chunks = []

    # Process each file individually with its own language-aware splitter
    for file_path in files_to_process:
        if not os.path.exists(file_path):
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue

        if not content.strip():
            continue

        try:
            rel_path = str(Path(file_path).relative_to(project_root)).replace("\\", "/") if project_root else os.path.basename(file_path)
        except Exception:
            rel_path = os.path.basename(file_path)

        doc = Document(
            page_content=redact_secrets(content),
            metadata={
                "source": file_path,
                "relative_path": rel_path,
                "project": project_name,
                "user_id": str(user_id) if user_id is not None else "",
                "filename": os.path.basename(file_path),
                "language": os.path.splitext(file_path)[1].lower().lstrip("."),
                "chunk_type": "commented_code",
            }
        )

        # FIX: Use per-file language splitter instead of always using Python splitter
        splitter = get_splitter_for_file(file_path)
        chunks = splitter.split_documents([doc])
        all_chunks.extend(chunks)

    if not all_chunks:
        print("No documents to ingest.")
        return 0

    print(f"  -> Split {len(files_to_process)} files into {len(all_chunks)} searchable chunks.")

    # Embed and store in Chroma DB
    project_db_path = settings.CHROMA_DB_DIR / project_name

    embeddings = model_router.embeddings()
    client = ensure_chroma_defaults(project_db_path)
    if replace_existing:
        collection_names = {
            getattr(collection, "name", str(collection))
            for collection in client.list_collections()
        }
        if VECTOR_COLLECTION_NAME in collection_names:
            client.delete_collection(VECTOR_COLLECTION_NAME)
            print(f"Cleared previous vector collection for {project_name}", flush=True)

    vectorstore = Chroma.from_documents(
        documents=all_chunks,
        embedding=embeddings,
        collection_name=VECTOR_COLLECTION_NAME,
        persist_directory=str(project_db_path),
        client=client,
    )
    close_vectorstore(vectorstore)

    print(f"Successfully saved vector database to {project_db_path}")

    # Invalidate stale vectorstore + query cache so next query loads fresh data
    invalidate_vectorstore_cache(project_name)

    return len(all_chunks)


def purge_project_from_chroma(project_name: str, strict: bool = False) -> bool:
    """Remove a project's Chroma store, including Windows-locked SQLite files."""
    try:
        project_db_path = settings.CHROMA_DB_DIR / project_name

        if project_db_path.exists():
            invalidate_vectorstore_cache(project_name)
            gc.collect()
            last_error = None
            for attempt in range(1, 6):
                try:
                    shutil.rmtree(project_db_path, onerror=remove_readonly)
                    print(f"Deleted vector database for {project_name}")
                    return True
                except Exception as exc:
                    last_error = exc
                    gc.collect()
                    time.sleep(0.4 * attempt)
            raise RuntimeError(f"Could not delete Chroma directory after retries: {last_error}")
        else:
            print(f"No vector database found at {project_db_path}")
            return True

    except Exception as e:
        print(f"Warning: Failed to execute purge routine: {str(e)}")
        if strict:
            raise
        return False
