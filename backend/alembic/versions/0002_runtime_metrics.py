"""add durable AI runtime metrics

Revision ID: 0002_runtime_metrics
Revises: 0001_durable_jobs
Create Date: 2026-09-23
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_runtime_metrics"
down_revision = "0001_durable_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "runtime_metrics" in set(sa.inspect(bind).get_table_names()):
        return
    op.create_table(
        "runtime_metrics",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trace_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("project_slug", sa.String()),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("component", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("prompt_id", sa.String()),
        sa.Column("prompt_version", sa.String()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("token_source", sa.String()),
        sa.Column("estimated_cost_microusd", sa.Integer()),
        sa.Column("error_type", sa.String()),
        sa.Column("attributes_json", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_runtime_metrics_user_created", "runtime_metrics", ["user_id", "created_at"])
    op.create_index("ix_runtime_metrics_trace", "runtime_metrics", ["trace_id"])
    op.create_index("ix_runtime_metrics_kind_status", "runtime_metrics", ["kind", "status"])


def downgrade() -> None:
    op.drop_table("runtime_metrics")
