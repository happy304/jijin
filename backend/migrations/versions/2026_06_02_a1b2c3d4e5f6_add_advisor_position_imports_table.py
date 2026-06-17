"""add advisor position imports table

Revision ID: a1b2c3d4e5f7
Revises: f0a1b2c3d4e5
Create Date: 2026-06-02 13:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a1b2c3d4e5f7"
down_revision: Union[str, Sequence[str], None] = "f0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JsonType = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")
_IdType = sa.Integer().with_variant(sa.BigInteger(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "advisor_position_imports",
        sa.Column("id", _IdType, autoincrement=True, nullable=False, comment="Advisor position import unique identifier"),
        sa.Column("filename", sa.String(length=255), nullable=False, comment="Original uploaded filename"),
        sa.Column("file_format", sa.String(length=20), nullable=False, comment="Import file format: csv/xls/xlsx"),
        sa.Column("status", sa.String(length=20), nullable=False, comment="Import status: completed/partial/failed"),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0", comment="Total parsed data rows"),
        sa.Column("imported_count", sa.Integer(), nullable=False, server_default="0", comment="Successfully imported position row count"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0", comment="Failed row count"),
        sa.Column("replaced_position_count", sa.Integer(), nullable=False, server_default="0", comment="Final persisted position count after replacement"),
        sa.Column("rows_json", _JsonType, nullable=True, comment="Per-row import results returned to frontend"),
        sa.Column("positions_json", _JsonType, nullable=True, comment="Normalized positions successfully imported in this batch"),
        sa.Column("metadata_json", _JsonType, nullable=True, comment="Additional import metadata"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=True, comment="Creation timestamp (UTC)"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_advisor_position_imports")),
    )
    op.create_index(op.f("ix_advisor_position_imports_status"), "advisor_position_imports", ["status"], unique=False)
    op.create_index(op.f("ix_advisor_position_imports_created_at"), "advisor_position_imports", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_advisor_position_imports_created_at"), table_name="advisor_position_imports")
    op.drop_index(op.f("ix_advisor_position_imports_status"), table_name="advisor_position_imports")
    op.drop_table("advisor_position_imports")
