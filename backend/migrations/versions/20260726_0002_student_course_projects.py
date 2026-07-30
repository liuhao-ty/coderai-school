"""Associate each student's editable engineering package with one text project.

Revision ID: 20260726_0002
Revises: 20260720_0001
Create Date: 2026-07-26 14:20:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260726_0002"
down_revision: Union[str, None] = "20260720_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX_NAME = "ux_projects_org_student_curriculum_course"
COURSE_INDEX_NAME = "ix_projects_curriculum_course_id"
FOREIGN_KEY_NAME = "fk_projects_curriculum_course_id"


def _project_columns() -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns("projects")}


def _project_indexes() -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("projects")}


def upgrade() -> None:
    bind = op.get_bind()
    if "curriculum_course_id" not in _project_columns():
        op.add_column("projects", sa.Column("curriculum_course_id", sa.Integer(), nullable=True))

    indexes = _project_indexes()
    if COURSE_INDEX_NAME not in indexes:
        op.create_index(COURSE_INDEX_NAME, "projects", ["curriculum_course_id"], unique=False)
    if INDEX_NAME not in indexes:
        op.create_index(
            INDEX_NAME,
            "projects",
            ["organization_id", "user_id", "curriculum_course_id"],
            unique=True,
        )

    if bind.dialect.name != "sqlite":
        foreign_keys = {
            tuple(item.get("constrained_columns") or [])
            for item in sa.inspect(bind).get_foreign_keys("projects")
        }
        if ("curriculum_course_id",) not in foreign_keys:
            op.create_foreign_key(
                FOREIGN_KEY_NAME,
                "projects",
                "curriculum_courses",
                ["curriculum_course_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        for foreign_key in sa.inspect(bind).get_foreign_keys("projects"):
            if tuple(foreign_key.get("constrained_columns") or []) != ("curriculum_course_id",):
                continue
            if foreign_key.get("name"):
                op.drop_constraint(foreign_key["name"], "projects", type_="foreignkey")
            break

    indexes = _project_indexes()
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name="projects")
    if COURSE_INDEX_NAME in indexes:
        op.drop_index(COURSE_INDEX_NAME, table_name="projects")
    if "curriculum_course_id" in _project_columns():
        op.drop_column("projects", "curriculum_course_id")
