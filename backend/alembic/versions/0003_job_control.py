"""add cancellation, delayed retries, and job events

Revision ID: 0003_job_control
Revises: 0002_runtime_metrics
Create Date: 2026-09-23
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_job_control"
down_revision = "0002_runtime_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("ingestion_jobs")}
    with op.batch_alter_table("ingestion_jobs") as batch:
        if "cancel_requested_at" not in columns:
            batch.add_column(sa.Column("cancel_requested_at", sa.DateTime(timezone=True)))
        if "next_attempt_at" not in columns:
            batch.add_column(sa.Column("next_attempt_at", sa.DateTime(timezone=True)))

    if "ingestion_job_events" not in set(inspector.get_table_names()):
        op.create_table(
            "ingestion_job_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("job_id", sa.Integer(), sa.ForeignKey("ingestion_jobs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("from_status", sa.String()),
            sa.Column("to_status", sa.String(), nullable=False),
            sa.Column("stage", sa.String()),
            sa.Column("message", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_ingestion_job_events_user_id", "ingestion_job_events", ["user_id"])
        op.create_index("ix_ingestion_job_events_job_created", "ingestion_job_events", ["job_id", "created_at"])


def downgrade() -> None:
    op.drop_table("ingestion_job_events")
    with op.batch_alter_table("ingestion_jobs") as batch:
        batch.drop_column("next_attempt_at")
        batch.drop_column("cancel_requested_at")
