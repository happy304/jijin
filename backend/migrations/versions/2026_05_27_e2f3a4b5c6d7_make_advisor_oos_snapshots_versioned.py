"""make advisor oos snapshots versioned

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-05-27 12:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    unique_names = {
        item.get("name")
        for item in inspector.get_unique_constraints("advisor_oos_snapshots")
    }
    if "uq_advisor_oos_snapshots_fund_code_risk_level" in unique_names:
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("advisor_oos_snapshots") as batch_op:
                batch_op.drop_constraint(
                    "uq_advisor_oos_snapshots_fund_code_risk_level",
                    type_="unique",
                )
        else:
            op.drop_constraint(
                "uq_advisor_oos_snapshots_fund_code_risk_level",
                "advisor_oos_snapshots",
                type_="unique",
            )

    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "snapshot_date",
            sa.Date(),
            nullable=True,
            comment="Logical validation snapshot date; defaults to updated_at for legacy rows",
        ),
    )
    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "config_hash",
            sa.String(length=64),
            nullable=True,
            comment="Hash of advisor / validation config used by this snapshot",
        ),
    )
    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "data_version",
            sa.String(length=100),
            nullable=True,
            comment="Input data version or data window fingerprint",
        ),
    )
    op.add_column(
        "advisor_oos_snapshots",
        sa.Column(
            "validation_window",
            sa.String(length=100),
            nullable=True,
            comment="Validation window descriptor",
        ),
    )

    op.create_index(
        op.f("ix_advisor_oos_snapshots_updated_at"),
        "advisor_oos_snapshots",
        ["updated_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_advisor_oos_snapshots_snapshot_date"),
        "advisor_oos_snapshots",
        ["snapshot_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_advisor_oos_snapshots_config_hash"),
        "advisor_oos_snapshots",
        ["config_hash"],
        unique=False,
    )

    op.execute(
        sa.text(
            "UPDATE advisor_oos_snapshots "
            "SET snapshot_date = updated_at "
            "WHERE snapshot_date IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_advisor_oos_snapshots_config_hash"), table_name="advisor_oos_snapshots")
    op.drop_index(op.f("ix_advisor_oos_snapshots_snapshot_date"), table_name="advisor_oos_snapshots")
    op.drop_index(op.f("ix_advisor_oos_snapshots_updated_at"), table_name="advisor_oos_snapshots")
    op.drop_column("advisor_oos_snapshots", "validation_window")
    op.drop_column("advisor_oos_snapshots", "data_version")
    op.drop_column("advisor_oos_snapshots", "config_hash")
    op.drop_column("advisor_oos_snapshots", "snapshot_date")
    op.create_unique_constraint(
        "uq_advisor_oos_snapshots_fund_code_risk_level",
        "advisor_oos_snapshots",
        ["fund_code", "risk_level"],
    )
