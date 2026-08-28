"""Classify projects and allow engineering package copies.

Revision ID: 20260826_0007
Revises: 20260823_0006
Create Date: 2026-08-26 16:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260826_0007"
down_revision: Union[str, None] = "20260823_0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UNIQUE_INDEX = "ux_projects_org_student_curriculum_course"
CATEGORY_INDEX = "ix_projects_project_category"


def _project_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "projects" not in inspector.get_table_names():
        return set()
    return {item["name"] for item in inspector.get_columns("projects")}


def _project_indexes() -> set[str]:
    if not _project_columns():
        return set()
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("projects")}


def upgrade() -> None:
    columns = _project_columns()
    if not columns:
        return

    category_added = "project_category" not in columns
    primary_added = "workspace_is_primary" not in columns
    if category_added:
        op.add_column(
            "projects",
            sa.Column(
                "project_category",
                sa.String(length=30),
                nullable=False,
                server_default="ai_generated",
            ),
        )
    if primary_added:
        op.add_column(
            "projects",
            sa.Column(
                "workspace_is_primary",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    columns = _project_columns()
    if "curriculum_course_id" in columns and (category_added or primary_added):
        op.execute(
            sa.text(
                "UPDATE projects SET project_category = CASE "
                "WHEN curriculum_course_id IS NOT NULL THEN 'course_workspace' "
                "ELSE 'ai_generated' END"
            )
        )
        op.execute(
            sa.text(
                "UPDATE projects SET workspace_is_primary = CASE "
                "WHEN curriculum_course_id IS NOT NULL THEN true ELSE false END"
            )
        )

    indexes = _project_indexes()
    required_unique_columns = {"organization_id", "user_id", "curriculum_course_id", "workspace_is_primary"}
    if required_unique_columns.issubset(columns):
        if UNIQUE_INDEX in indexes:
            op.drop_index(UNIQUE_INDEX, table_name="projects")
        op.create_index(
            UNIQUE_INDEX,
            "projects",
            ["organization_id", "user_id", "curriculum_course_id"],
            unique=True,
            sqlite_where=sa.text(
                "workspace_is_primary = 1 AND user_id IS NOT NULL AND curriculum_course_id IS NOT NULL"
            ),
            postgresql_where=sa.text(
                "workspace_is_primary = true AND user_id IS NOT NULL AND curriculum_course_id IS NOT NULL"
            ),
        )
    if CATEGORY_INDEX not in _project_indexes():
        op.create_index(CATEGORY_INDEX, "projects", ["project_category"])


def downgrade() -> None:
    columns = _project_columns()
    if not columns:
        return
    indexes = _project_indexes()
    if CATEGORY_INDEX in indexes:
        op.drop_index(CATEGORY_INDEX, table_name="projects")
    if UNIQUE_INDEX in indexes:
        op.drop_index(UNIQUE_INDEX, table_name="projects")

    required_unique_columns = {"organization_id", "user_id", "curriculum_course_id"}
    if "workspace_is_primary" in columns and "curriculum_course_id" in columns:
        op.execute(
            sa.text(
                "UPDATE projects SET curriculum_course_id = NULL "
                "WHERE workspace_is_primary = false"
            )
        )
    if required_unique_columns.issubset(columns):
        op.create_index(
            UNIQUE_INDEX,
            "projects",
            ["organization_id", "user_id", "curriculum_course_id"],
            unique=True,
        )
    if "workspace_is_primary" in columns:
        op.drop_column("projects", "workspace_is_primary")
    if "project_category" in columns:
        op.drop_column("projects", "project_category")
