"""Store structured answers for student engineering-package workspaces.

Revision ID: 20260727_0003
Revises: 20260726_0002
Create Date: 2026-07-27 18:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260727_0003"
down_revision: Union[str, None] = "20260726_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _project_columns() -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns("projects")}


def upgrade() -> None:
    if "workspace_answers_json" not in _project_columns():
        op.add_column(
            "projects",
            sa.Column("workspace_answers_json", sa.Text(), nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    if "workspace_answers_json" in _project_columns():
        op.drop_column("projects", "workspace_answers_json")
