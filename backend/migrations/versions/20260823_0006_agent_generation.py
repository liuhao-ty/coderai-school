"""Add persistent student agent conversations and async generation jobs.

Revision ID: 20260823_0006
Revises: 20260823_0005
Create Date: 2026-08-23 10:30:00
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260823_0006"
down_revision: Union[str, None] = "20260823_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _create_index_if_missing(table_name: str, index_name: str, columns: list[str]) -> None:
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)}
    if index_name not in indexes:
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    tables = _tables()
    provider_columns = (
        {item["name"] for item in sa.inspect(op.get_bind()).get_columns("ai_providers")}
        if "ai_providers" in tables
        else set()
    )
    if "ai_providers" in tables and "student_selectable" not in provider_columns:
        op.add_column(
            "ai_providers",
            sa.Column("student_selectable", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    if "agent_conversations" not in tables:
        op.create_table(
        "agent_conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False, server_default="新对话"),
        sa.Column("selected_provider_id", sa.Integer(), sa.ForeignKey("ai_providers.id"), nullable=True),
        sa.Column("memory_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    for name, columns in (
        ("ix_agent_conversations_organization_id", ["organization_id"]),
        ("ix_agent_conversations_user_id", ["user_id"]),
        ("ix_agent_conversations_status", ["status"]),
    ):
        _create_index_if_missing("agent_conversations", name, columns)

    if "agent_messages" not in tables:
        op.create_table(
        "agent_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("agent_conversations.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="completed"),
        sa.Column("provider_id", sa.Integer(), sa.ForeignKey("ai_providers.id"), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("tool_suggestion_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("organization_id", "conversation_id", "sequence", name="ux_agent_messages_org_sequence"),
        )
    for name, columns in (
        ("ix_agent_messages_organization_id", ["organization_id"]),
        ("ix_agent_messages_conversation_id", ["conversation_id"]),
        ("ix_agent_messages_user_id", ["user_id"]),
    ):
        _create_index_if_missing("agent_messages", name, columns)

    if "ai_generation_jobs" not in tables:
        op.create_table(
        "ai_generation_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("classroom_id", sa.Integer(), sa.ForeignKey("classrooms.id"), nullable=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("agent_conversations.id"), nullable=True),
        sa.Column("assistant_message_id", sa.Integer(), sa.ForeignKey("agent_messages.id"), nullable=True),
        sa.Column("provider_id", sa.Integer(), sa.ForeignKey("ai_providers.id"), nullable=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("client_request_id", sa.String(length=80), nullable=False),
        sa.Column("capability", sa.String(length=20), nullable=False),
        sa.Column("operation", sa.String(length=40), nullable=False, server_default="generate"),
        sa.Column("model", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("request_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_code", sa.String(length=80), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("organization_id", "user_id", "client_request_id", name="ux_ai_jobs_org_user_request"),
        )
    for name, columns in (
        ("ix_ai_generation_jobs_organization_id", ["organization_id"]),
        ("ix_ai_generation_jobs_user_id", ["user_id"]),
        ("ix_ai_generation_jobs_classroom_id", ["classroom_id"]),
        ("ix_ai_generation_jobs_conversation_id", ["conversation_id"]),
        ("ix_ai_generation_jobs_capability", ["capability"]),
        ("ix_ai_generation_jobs_status", ["status"]),
    ):
        _create_index_if_missing("ai_generation_jobs", name, columns)

    if "agent_artifacts" not in tables:
        op.create_table(
        "agent_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("agent_conversations.id"), nullable=False),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("agent_messages.id"), nullable=True),
        sa.Column("generation_job_id", sa.Integer(), sa.ForeignKey("ai_generation_jobs.id"), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("saved_project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("artifact_type", sa.String(length=30), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("file_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("original_file_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("mime_type", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="available"),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    for name, columns in (
        ("ix_agent_artifacts_organization_id", ["organization_id"]),
        ("ix_agent_artifacts_conversation_id", ["conversation_id"]),
        ("ix_agent_artifacts_message_id", ["message_id"]),
        ("ix_agent_artifacts_generation_job_id", ["generation_job_id"]),
        ("ix_agent_artifacts_user_id", ["user_id"]),
        ("ix_agent_artifacts_status", ["status"]),
    ):
        _create_index_if_missing("agent_artifacts", name, columns)

    video_columns = (
        {item["name"] for item in sa.inspect(op.get_bind()).get_columns("video_tasks")}
        if "video_tasks" in tables
        else set()
    )
    if "video_tasks" in tables and "generation_job_id" not in video_columns:
        op.add_column(
            "video_tasks",
            sa.Column("generation_job_id", sa.Integer(), sa.ForeignKey("ai_generation_jobs.id"), nullable=True),
        )
        op.create_index("ix_video_tasks_generation_job_id", "video_tasks", ["generation_job_id"])

    bind = op.get_bind()
    provider_ids: list[int] = []
    route_rows = bind.execute(sa.text(
        "SELECT provider_ids_json FROM ai_provider_routes WHERE capability = 'text'"
    )).fetchall() if "ai_provider_routes" in tables else []
    for row in route_rows:
        try:
            values = json.loads(row[0] or "[]")
        except (TypeError, json.JSONDecodeError):
            continue
        for value in values if isinstance(values, list) else []:
            if isinstance(value, int) and value > 0 and value not in provider_ids:
                provider_ids.append(value)
    if provider_ids and "ai_providers" in tables:
        bind.execute(
            sa.text("UPDATE ai_providers SET student_selectable = TRUE WHERE enabled = TRUE AND id IN :provider_ids")
            .bindparams(sa.bindparam("provider_ids", expanding=True)),
            {"provider_ids": provider_ids},
        )


def downgrade() -> None:
    video_columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("video_tasks")}
    if "generation_job_id" in video_columns:
        op.drop_column("video_tasks", "generation_job_id")
    for table_name in ("agent_artifacts", "ai_generation_jobs", "agent_messages", "agent_conversations"):
        if table_name in sa.inspect(op.get_bind()).get_table_names():
            op.drop_table(table_name)
    provider_columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("ai_providers")}
    if "student_selectable" in provider_columns:
        op.drop_column("ai_providers", "student_selectable")
