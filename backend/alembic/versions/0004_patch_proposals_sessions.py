"""add patch proposals and session versioning

Revision ID: 0004_patch_sessions
Revises: 0003_job_control
Create Date: 2026-09-23
"""
from alembic import op
import sqlalchemy as sa


revision = "0004_patch_sessions"
down_revision = "0003_job_control"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    with op.batch_alter_table("users") as batch:
        if "token_version" not in user_columns:
            batch.add_column(sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"))

    if "patch_proposals" not in set(inspector.get_table_names()):
        op.create_table(
            "patch_proposals",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("file_path", sa.Text(), nullable=False),
            sa.Column("base_checksum", sa.String(), nullable=False),
            sa.Column("proposed_content", sa.Text(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("applied_at", sa.DateTime(timezone=True)),
        )
        op.create_index("ix_patch_proposals_user_id", "patch_proposals", ["user_id"])
        op.create_index("ix_patch_proposals_user_status", "patch_proposals", ["user_id", "status"])
        op.create_index("ix_patch_proposals_project_created", "patch_proposals", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_table("patch_proposals")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("token_version")
