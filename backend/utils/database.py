from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from config import settings

settings.create_required_directories()

def build_engine_options(database_url: str) -> dict:
    """Return safe SQLAlchemy options for local and pooled production databases."""
    engine_options = {"pool_pre_ping": True}

    if database_url.startswith("sqlite"):
        engine_options["connect_args"] = {"check_same_thread": False}
        return engine_options

    engine_options.update({
        "connect_args": {
            "connect_timeout": 5,
            # Supabase's transaction-mode pooler reuses server connections.
            # Psycopg client-side prepared statements can therefore collide
            # across sessions with DuplicatePreparedStatement during startup.
            "prepare_threshold": None,
        },
        "pool_size": 5,
        "max_overflow": 5,
        "pool_timeout": 10,
        "pool_recycle": 300,
    })
    return engine_options


database_url = settings.resolved_database_url
engine_options = build_engine_options(database_url)

engine = create_engine(database_url, **engine_options)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    """Dependency to yield a database session and close it automatically."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
