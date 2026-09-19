from pathlib import Path

import chromadb
from chromadb.config import DEFAULT_DATABASE, DEFAULT_TENANT


VECTOR_COLLECTION_NAME = "langchain"


def ensure_chroma_defaults(persist_directory: str | Path):
    """Create Chroma's default tenant/database for a fresh persistent store."""
    client = chromadb.PersistentClient(path=str(persist_directory))
    admin = getattr(client, "_admin_client", None)
    if admin is None:
        return client

    try:
        admin.get_tenant(DEFAULT_TENANT)
    except Exception:
        admin.create_tenant(DEFAULT_TENANT)

    try:
        admin.get_database(DEFAULT_DATABASE, tenant=DEFAULT_TENANT)
    except Exception:
        admin.create_database(DEFAULT_DATABASE, tenant=DEFAULT_TENANT)

    return client
