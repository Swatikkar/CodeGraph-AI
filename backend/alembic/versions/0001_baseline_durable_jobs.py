"""baseline existing schema and add durable ingestion jobs

Revision ID: 0001_durable_jobs
Revises:
Create Date: 2026-09-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_durable_jobs"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("email", sa.String(), nullable=False),
            sa.Column("hashed_password", sa.String(), nullable=False),
            sa.UniqueConstraint("email"),
        )
        op.create_index("ix_users_email", "users", ["email"], unique=True)
    if "projects" not in tables:
        op.create_table(
            "projects",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("display_name", sa.String(), nullable=False),
            sa.Column("source_type", sa.String(), nullable=False),
            sa.Column("source_url", sa.Text()),
            sa.Column("status", sa.String(), nullable=False, server_default="processing"),
            sa.Column("stage", sa.String(), nullable=False, server_default="created"),
            sa.Column("message", sa.Text()),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error", sa.Text()),
            sa.Column("vector_index_status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("vector_indexed_at", sa.DateTime(timezone=True)),
            sa.Column("vector_index_error", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("user_id", "slug", name="uq_projects_user_slug"),
        )
        op.create_index("ix_projects_user_id", "projects", ["user_id"])
        op.create_index("ix_projects_slug", "projects", ["slug"])
    tables = set(sa.inspect(bind).get_table_names())
    child_tables = {
        "project_files": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False), sa.Column("path", sa.Text(), nullable=False), sa.Column("content", sa.Text(), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"), sa.Column("checksum", sa.String(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("project_id", "path", name="uq_project_files_project_path"),
        ],
        "project_artifacts": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False), sa.Column("artifact_type", sa.String(), nullable=False), sa.Column("content", sa.Text(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("project_id", "artifact_type", name="uq_project_artifacts_project_type"),
        ],
        "project_chunks": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False), sa.Column("path", sa.Text(), nullable=False), sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False), sa.Column("embedding", sa.Text()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("project_id", "path", "chunk_index", name="uq_project_chunks_project_path_index"),
        ],
        "chat_messages": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False), sa.Column("role", sa.String(), nullable=False), sa.Column("content", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        ],
    }
    for table_name, columns in child_tables.items():
        if table_name not in tables:
            op.create_table(table_name, *columns)
            op.create_index(f"ix_{table_name}_project_id", table_name, ["project_id"])
            op.create_index(f"ix_{table_name}_user_id", table_name, ["user_id"])
    if "ingestion_jobs" not in tables:
        op.create_table(
            "ingestion_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("project_slug", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="queued"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.Column("error_code", sa.String()),
            sa.Column("error_message", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("project_id", name="uq_ingestion_jobs_project_id"),
        )
        op.create_index("ix_ingestion_jobs_user_id", "ingestion_jobs", ["user_id"])
        op.create_index("ix_ingestion_jobs_claim", "ingestion_jobs", ["status", "created_at"])
        op.create_index("ix_ingestion_jobs_heartbeat", "ingestion_jobs", ["status", "heartbeat_at"])

    inspector = sa.inspect(bind)
    project_columns = {column["name"] for column in inspector.get_columns("projects")}
    additions = {
        "vector_index_status": sa.Column("vector_index_status", sa.String(), nullable=False, server_default="pending"),
        "vector_indexed_at": sa.Column("vector_indexed_at", sa.DateTime(timezone=True), nullable=True),
        "vector_index_error": sa.Column("vector_index_error", sa.Text(), nullable=True),
    }
    for name, column in additions.items():
        if name not in project_columns:
            op.add_column("projects", column)


def downgrade() -> None:
    # Deliberately preserve project data; destructive rollback is manual.
    pass
