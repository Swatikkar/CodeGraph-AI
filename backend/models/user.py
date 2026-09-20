# models/user.py
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship

from utils.database import Base

class UserModel(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)



class ProjectModel(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("user_id", "slug", name="uq_projects_user_slug"),)

    id = Column(Integer, primary_key=True, index=True)
    # String supports both local integer IDs and Supabase UUID subjects.
    user_id = Column(String, index=True, nullable=False)
    slug = Column(String, index=True, nullable=False)
    display_name = Column(String, nullable=False)
    source_type = Column(String, nullable=False)
    source_url = Column(Text, nullable=True)
    status = Column(String, nullable=False, default="processing")
    stage = Column(String, nullable=False, default="created")
    message = Column(Text, nullable=True)
    progress = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    vector_index_status = Column(String, nullable=False, default="pending")
    vector_indexed_at = Column(DateTime(timezone=True), nullable=True)
    vector_index_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    files = relationship("ProjectFileModel", back_populates="project", cascade="all, delete-orphan")
    artifacts = relationship("ProjectArtifactModel", back_populates="project", cascade="all, delete-orphan")
    chunks = relationship("ProjectChunkModel", back_populates="project", cascade="all, delete-orphan")
    chat_messages = relationship("ChatMessageModel", back_populates="project", cascade="all, delete-orphan")
    ingestion_job = relationship("IngestionJobModel", back_populates="project", cascade="all, delete-orphan", uselist=False)


class ProjectFileModel(Base):
    __tablename__ = "project_files"
    __table_args__ = (UniqueConstraint("project_id", "path", name="uq_project_files_project_path"),)

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    path = Column(Text, nullable=False)
    content = Column(Text, nullable=False)
    size_bytes = Column(Integer, nullable=False, default=0)
    checksum = Column(String, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("ProjectModel", back_populates="files")


class ProjectArtifactModel(Base):
    __tablename__ = "project_artifacts"
    __table_args__ = (UniqueConstraint("project_id", "artifact_type", name="uq_project_artifacts_project_type"),)

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    artifact_type = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("ProjectModel", back_populates="artifacts")


class ProjectChunkModel(Base):
    __tablename__ = "project_chunks"
    __table_args__ = (UniqueConstraint("project_id", "path", "chunk_index", name="uq_project_chunks_project_path_index"),)

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    path = Column(Text, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("ProjectModel", back_populates="chunks")


class ChatMessageModel(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id = Column(String, index=True, nullable=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project = relationship("ProjectModel", back_populates="chat_messages")


class IngestionJobModel(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_ingestion_jobs_project_id"),
        Index("ix_ingestion_jobs_claim", "status", "created_at"),
        Index("ix_ingestion_jobs_heartbeat", "status", "heartbeat_at"),
    )

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String, index=True, nullable=False)
    project_slug = Column(String, nullable=False)
    status = Column(String, nullable=False, default="queued")
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("ProjectModel", back_populates="ingestion_job")
