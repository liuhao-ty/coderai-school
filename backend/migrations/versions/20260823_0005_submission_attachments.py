"""Store local course uploads as submission attachments.

Revision ID: 20260823_0005
Revises: 20260815_0004
Create Date: 2026-08-23 10:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260823_0005"
down_revision: Union[str, None] = "20260815_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}


def _make_nullable(table_name: str, column_name: str) -> None:
    columns = {item["name"]: item for item in sa.inspect(op.get_bind()).get_columns(table_name)}
    column = columns.get(column_name)
    if column is None:
        op.add_column(table_name, sa.Column(column_name, sa.Integer(), nullable=True))
        return
    if column.get("nullable", True):
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column(column_name, existing_type=sa.Integer(), nullable=True)
    else:
        op.alter_column(table_name, column_name, existing_type=sa.Integer(), nullable=True)


def upgrade() -> None:
    submission_columns = _columns("task_submissions")
    legacy_submission_columns = (
        ("status", sa.Column("status", sa.String(length=30), nullable=False, server_default="submitted")),
        ("feedback", sa.Column("feedback", sa.Text(), nullable=False, server_default="")),
        ("score", sa.Column("score", sa.Integer(), nullable=True)),
        ("version_count", sa.Column("version_count", sa.Integer(), nullable=False, server_default=sa.text("1"))),
        ("max_score_snapshot", sa.Column("max_score_snapshot", sa.Integer(), nullable=False, server_default=sa.text("100"))),
    )
    for name, column in legacy_submission_columns:
        if name not in submission_columns:
            op.add_column("task_submissions", column)
    if "source_type" not in submission_columns:
        op.add_column(
            "task_submissions",
            sa.Column("source_type", sa.String(length=20), nullable=False, server_default="project"),
        )
    _make_nullable("task_submissions", "project_id")

    version_columns = _columns("submission_versions")
    additions = (
        ("source_type", sa.Column("source_type", sa.String(length=20), nullable=False, server_default="project")),
        ("review_status", sa.Column("review_status", sa.String(length=30), nullable=False, server_default="submitted")),
        ("feedback_snapshot", sa.Column("feedback_snapshot", sa.Text(), nullable=False, server_default="")),
        ("score_snapshot", sa.Column("score_snapshot", sa.Integer(), nullable=True)),
        ("max_score_snapshot", sa.Column("max_score_snapshot", sa.Integer(), nullable=False, server_default=sa.text("100"))),
        ("file_name_snapshot", sa.Column("file_name_snapshot", sa.String(length=255), nullable=False, server_default="")),
        ("mime_type_snapshot", sa.Column("mime_type_snapshot", sa.String(length=120), nullable=False, server_default="")),
        ("file_size_snapshot", sa.Column("file_size_snapshot", sa.Integer(), nullable=False, server_default=sa.text("0"))),
    )
    for name, column in additions:
        if name not in version_columns:
            op.add_column("submission_versions", column)
    _make_nullable("submission_versions", "project_id")

    if "submission_attachments" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "submission_attachments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("organization_id", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("submission_id", sa.Integer(), sa.ForeignKey("task_submissions.id"), nullable=False),
            sa.Column("version_id", sa.Integer(), sa.ForeignKey("submission_versions.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("classroom_id", sa.Integer(), sa.ForeignKey("classrooms.id"), nullable=True),
            sa.Column("title", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("file_path", sa.Text(), nullable=False, server_default=""),
            sa.Column("original_file_name", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("mime_type", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("file_extension", sa.String(length=20), nullable=False, server_default=""),
            sa.Column("file_size", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("checksum_sha256", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("safety_status", sa.String(length=30), nullable=False, server_default="approved"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("organization_id", "version_id", name="ux_submission_attachments_org_version"),
        )
        op.create_index("ix_submission_attachments_organization_id", "submission_attachments", ["organization_id"])
        op.create_index("ix_submission_attachments_submission_id", "submission_attachments", ["submission_id"])
        op.create_index("ix_submission_attachments_version_id", "submission_attachments", ["version_id"])
        op.create_index("ix_submission_attachments_user_id", "submission_attachments", ["user_id"])
        op.create_index("ix_submission_attachments_classroom_id", "submission_attachments", ["classroom_id"])

    bind = op.get_bind()
    bind.execute(sa.text("UPDATE task_submissions SET source_type = 'project' WHERE source_type IS NULL OR source_type = ''"))
    bind.execute(sa.text("UPDATE submission_versions SET source_type = 'project' WHERE source_type IS NULL OR source_type = ''"))
    bind.execute(sa.text(
        "UPDATE submission_versions SET "
        "file_name_snapshot = COALESCE((SELECT original_file_name FROM projects WHERE projects.id = submission_versions.project_id), ''), "
        "mime_type_snapshot = COALESCE((SELECT mime_type FROM projects WHERE projects.id = submission_versions.project_id), ''), "
        "file_size_snapshot = COALESCE((SELECT file_size FROM projects WHERE projects.id = submission_versions.project_id), 0) "
        "WHERE project_id IS NOT NULL AND (file_name_snapshot = '' OR mime_type_snapshot = '' OR file_size_snapshot = 0)"
    ))
    bind.execute(sa.text(
        "UPDATE submission_versions SET "
        "review_status = (SELECT status FROM task_submissions WHERE task_submissions.id = submission_versions.submission_id), "
        "feedback_snapshot = (SELECT feedback FROM task_submissions WHERE task_submissions.id = submission_versions.submission_id), "
        "score_snapshot = (SELECT score FROM task_submissions WHERE task_submissions.id = submission_versions.submission_id), "
        "max_score_snapshot = (SELECT max_score_snapshot FROM task_submissions WHERE task_submissions.id = submission_versions.submission_id) "
        "WHERE version_number = (SELECT version_count FROM task_submissions WHERE task_submissions.id = submission_versions.submission_id)"
    ))


def downgrade() -> None:
    if "submission_attachments" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("submission_attachments")
    version_columns = _columns("submission_versions")
    for column_name in (
        "file_size_snapshot", "mime_type_snapshot", "file_name_snapshot",
        "max_score_snapshot", "score_snapshot", "feedback_snapshot", "review_status", "source_type",
    ):
        if column_name in version_columns:
            op.drop_column("submission_versions", column_name)
    submission_columns = _columns("task_submissions")
    if "source_type" in submission_columns:
        op.drop_column("task_submissions", "source_type")
