# models/user.py
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import relationship

from utils.database import Base

class UserModel(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    token_version = Column(Integer, nullable=False, default=0)



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
    patch_proposals = relationship("PatchProposalModel", back_populates="project", cascade="all, delete-orphan")


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
    cancel_requested_at = Column(DateTime(timezone=True), nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    error_code = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    project = relationship("ProjectModel", back_populates="ingestion_job")
    events = relationship("IngestionJobEventModel", back_populates="job", cascade="all, delete-orphan")


class IngestionJobEventModel(Base):
    __tablename__ = "ingestion_job_events"
    __table_args__ = (Index("ix_ingestion_job_events_job_created", "job_id", "created_at"),)

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("ingestion_jobs.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String, index=True, nullable=False)
    from_status = Column(String, nullable=True)
    to_status = Column(String, nullable=False)
    stage = Column(String, nullable=True)
    message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    job = relationship("IngestionJobModel", back_populates="events")


class PatchProposalModel(Base):
    __tablename__ = "patch_proposals"
    __table_args__ = (
        Index("ix_patch_proposals_user_status", "user_id", "status"),
        Index("ix_patch_proposals_project_created", "project_id", "created_at"),
    )

    id = Column(String, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(String, index=True, nullable=False)
    file_path = Column(Text, nullable=False)
    base_checksum = Column(String, nullable=False)
    proposed_content = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    applied_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("ProjectModel", back_populates="patch_proposals")


class RuntimeMetricModel(Base):
    __tablename__ = "runtime_metrics"
    __table_args__ = (
        Index("ix_runtime_metrics_user_created", "user_id", "created_at"),
        Index("ix_runtime_metrics_trace", "trace_id"),
        Index("ix_runtime_metrics_kind_status", "kind", "status"),
    )

    id = Column(Integer, primary_key=True)
    trace_id = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    project_slug = Column(String, nullable=True)
    kind = Column(String, nullable=False)
    component = Column(String, nullable=False)
    status = Column(String, nullable=False)
    prompt_id = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    token_source = Column(String, nullable=True)
    estimated_cost_microusd = Column(Integer, nullable=True)
    error_type = Column(String, nullable=True)
    attributes_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
