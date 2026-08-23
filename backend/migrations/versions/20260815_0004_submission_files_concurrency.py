"""Add course submission files and concurrency-safe submission keys.

Revision ID: 20260815_0004
Revises: 20260727_0003
Create Date: 2026-08-15 12:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260815_0004"
down_revision: Union[str, None] = "20260727_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


SUBMISSION_INDEX = "ux_task_submissions_org_task_user"
VERSION_INDEX = "ux_submission_versions_org_submission_version"
DEFAULT_EXTENSIONS_JSON = (
    '[".md",".txt",".pdf",".zip",".sb3",".py",".html",".css",".js",".ts",'
    '".json",".csv",".docx",".pptx",".xlsx",".png",".jpg",".jpeg",".webp",".mp4"]'
)


def _columns(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}


def _unique_column_sets(table_name: str) -> set[tuple[str, ...]]:
    inspector = sa.inspect(op.get_bind())
    result = {
        tuple(item.get("column_names") or [])
        for item in inspector.get_unique_constraints(table_name)
    }
    result.update(
        tuple(item.get("column_names") or [])
        for item in inspector.get_indexes(table_name)
        if item.get("unique")
    )
    return result


def _deduplicate_submissions() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, organization_id, task_id, user_id "
        "FROM task_submissions "
        "ORDER BY organization_id, task_id, user_id, COALESCE(updated_at, created_at) DESC, id DESC"
    )).mappings().all()
    grouped: dict[tuple[int, int, int], list[int]] = {}
    for row in rows:
        key = (int(row["organization_id"]), int(row["task_id"]), int(row["user_id"]))
        grouped.setdefault(key, []).append(int(row["id"]))

    for submission_ids in grouped.values():
        if len(submission_ids) < 2:
            continue
        canonical_id = submission_ids[0]
        for duplicate_id in submission_ids[1:]:
            bind.execute(
                sa.text(
                    "UPDATE submission_versions SET version_number = -(1000000000 + id) "
                    "WHERE submission_id = :duplicate_id"
                ),
                {"duplicate_id": duplicate_id},
            )
            bind.execute(
                sa.text(
                    "UPDATE submission_versions SET submission_id = :canonical_id "
                    "WHERE submission_id = :duplicate_id"
                ),
                {"canonical_id": canonical_id, "duplicate_id": duplicate_id},
            )
            bind.execute(
                sa.text("DELETE FROM task_submissions WHERE id = :duplicate_id"),
                {"duplicate_id": duplicate_id},
            )
    canonical_ids = bind.execute(sa.text("SELECT id FROM task_submissions ORDER BY id")).fetchall()
    for canonical_row in canonical_ids:
        canonical_id = int(canonical_row[0])
        versions = bind.execute(
            sa.text(
                "SELECT id FROM submission_versions WHERE submission_id = :submission_id "
                "ORDER BY created_at, id"
            ),
            {"submission_id": canonical_id},
        ).fetchall()
        for version in versions:
            bind.execute(
                sa.text("UPDATE submission_versions SET version_number = :number WHERE id = :version_id"),
                {"number": -(1_000_000_000 + int(version[0])), "version_id": int(version[0])},
            )
        for version_number, version in enumerate(versions, start=1):
            bind.execute(
                sa.text("UPDATE submission_versions SET version_number = :number WHERE id = :version_id"),
                {"number": version_number, "version_id": int(version[0])},
            )
        bind.execute(
            sa.text("UPDATE task_submissions SET version_count = :count WHERE id = :submission_id"),
            {"count": max(len(versions), 1), "submission_id": canonical_id},
        )


def upgrade() -> None:
    if "submission_extensions_json" not in _columns("curriculum_courses"):
        op.add_column(
            "curriculum_courses",
            sa.Column("submission_extensions_json", sa.Text(), nullable=False, server_default=sa.text(f"'{DEFAULT_EXTENSIONS_JSON}'")),
        )
    if "submission_max_bytes" not in _columns("curriculum_courses"):
        op.add_column(
            "curriculum_courses",
            sa.Column("submission_max_bytes", sa.Integer(), nullable=False, server_default=sa.text("20971520")),
        )
    if "original_file_name" not in _columns("projects"):
        op.add_column("projects", sa.Column("original_file_name", sa.String(length=255), nullable=False, server_default=""))
    if "mime_type" not in _columns("projects"):
        op.add_column("projects", sa.Column("mime_type", sa.String(length=120), nullable=False, server_default=""))
    if "file_size" not in _columns("projects"):
        op.add_column("projects", sa.Column("file_size", sa.Integer(), nullable=False, server_default=sa.text("0")))

    _deduplicate_submissions()
    submission_key = ("organization_id", "task_id", "user_id")
    if submission_key not in _unique_column_sets("task_submissions"):
        op.create_index(SUBMISSION_INDEX, "task_submissions", list(submission_key), unique=True)
    version_key = ("organization_id", "submission_id", "version_number")
    if version_key not in _unique_column_sets("submission_versions"):
        op.create_index(VERSION_INDEX, "submission_versions", list(version_key), unique=True)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    submission_indexes = {item["name"] for item in inspector.get_indexes("task_submissions")}
    version_indexes = {item["name"] for item in inspector.get_indexes("submission_versions")}
    if VERSION_INDEX in version_indexes:
        op.drop_index(VERSION_INDEX, table_name="submission_versions")
    if SUBMISSION_INDEX in submission_indexes:
        op.drop_index(SUBMISSION_INDEX, table_name="task_submissions")
    for table_name, column_name in (
        ("projects", "file_size"),
        ("projects", "mime_type"),
        ("projects", "original_file_name"),
        ("curriculum_courses", "submission_max_bytes"),
        ("curriculum_courses", "submission_extensions_json"),
    ):
        if column_name in _columns(table_name):
            op.drop_column(table_name, column_name)
