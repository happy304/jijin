"""add simulation_runs table

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-05-18

Monte Carlo simulation prediction feature. Stores simulation run
configurations, status, and results (metrics + percentile paths).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type() -> sa.JSON:
    return (
        postgresql.JSONB(astext_type=sa.Text())
        if op.get_bind().dialect.name == "postgresql"
        else sa.JSON()
    )


def upgrade() -> None:
    json_type = _json_type()
    op.create_table(
        "simulation_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="Simulation run unique identifier"),
        sa.Column("strategy_id", sa.BigInteger(), nullable=True, comment="Reference to strategies.id"),
        sa.Column("horizon_days", sa.Integer(), nullable=False, server_default="252", comment="Forecast horizon in trading days"),
        sa.Column("num_simulations", sa.Integer(), nullable=False, server_default="10000", comment="Number of Monte Carlo paths"),
        sa.Column("method", sa.String(20), nullable=False, server_default="gbm", comment="Simulation method: gbm/bootstrap/hybrid"),
        sa.Column("initial_capital", sa.Numeric(20, 2), nullable=True, comment="Initial capital amount"),
        sa.Column("target_return", sa.Numeric(8, 4), nullable=True, comment="Target return rate (e.g. 0.15 = 15%)"),
        sa.Column("confidence_levels", json_type, nullable=True, comment="Confidence levels for VaR/CVaR"),
        sa.Column("lookback_days", sa.Integer(), nullable=True, server_default="504", comment="Historical lookback days for parameter estimation"),
        sa.Column("status", sa.String(20), nullable=True, server_default="pending", comment="Status: pending/running/done/failed"),
        sa.Column("progress", sa.Numeric(5, 2), nullable=True, server_default="0", comment="Progress percentage 0-100"),
        sa.Column("metrics", json_type, nullable=True, comment="Simulation result metrics (JSON)"),
        sa.Column("percentile_paths", json_type, nullable=True, comment="Percentile paths for fan chart visualization (JSON)"),
        sa.Column("error_msg", sa.Text(), nullable=True, comment="Error message if failed"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True, comment="Execution start timestamp"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, comment="Execution finish timestamp"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now(), comment="Record creation timestamp"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_simulation_runs")),
    )
    # Index for querying by strategy
    op.create_index(
        op.f("ix_simulation_runs_strategy_id"),
        "simulation_runs",
        ["strategy_id"],
    )
    # Index for querying by status
    op.create_index(
        op.f("ix_simulation_runs_status"),
        "simulation_runs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_simulation_runs_status"), table_name="simulation_runs")
    op.drop_index(op.f("ix_simulation_runs_strategy_id"), table_name="simulation_runs")
    op.drop_table("simulation_runs")
