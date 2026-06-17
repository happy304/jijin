"""add strategy snapshot to simulation_runs

Revision ID: 1a2b3c4d5e6f
Revises: f6a7b8c9d0e1
Create Date: 2026-05-23

Persist strategy configuration snapshots on simulation runs so historical
records keep the original strategy name/config even after the strategy is
renamed or deleted.
"""
from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "1a2b3c4d5e6f"
down_revision: Union[str, None] = "9a343882301a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _build_snapshot_from_row(row: dict) -> dict:
    strategy_id = row.get("strategy_id")
    strategy_name = row.get("strategy_name")
    metrics = row.get("metrics") or {}
    if isinstance(metrics, str):
        try:
            metrics = json.loads(metrics)
        except (TypeError, ValueError, json.JSONDecodeError):
            metrics = {}

    strategy_params = row.get("strategy_params")
    if isinstance(strategy_params, str):
        try:
            strategy_params = json.loads(strategy_params)
        except (TypeError, ValueError, json.JSONDecodeError):
            strategy_params = None

    strategy_universe = row.get("strategy_universe")
    if isinstance(strategy_universe, str):
        try:
            strategy_universe = json.loads(strategy_universe)
        except (TypeError, ValueError, json.JSONDecodeError):
            strategy_universe = None

    funds_used = metrics.get("funds_used")
    universe = {"fund_codes": funds_used} if isinstance(funds_used, list) else None
    return {
        "id": strategy_id,
        "name": strategy_name or (f"已删除策略 #{strategy_id}" if strategy_id is not None else None),
        "strategy_type": row.get("strategy_type") or metrics.get("strategy_type"),
        "params": strategy_params or metrics.get("strategy_params") or {},
        "universe": strategy_universe or universe,
        "benchmark": row.get("strategy_benchmark"),
    }


POSTGRES_SNAPSHOT_SQL = """
UPDATE simulation_runs AS sr
SET strategy_snapshot = sub.snapshot
FROM (
    SELECT
        sr_inner.id,
        CASE
            WHEN s.id IS NOT NULL THEN jsonb_build_object(
                'id', s.id,
                'name', s.name,
                'strategy_type', s.strategy_type,
                'params', COALESCE(s.params, '{}'::jsonb),
                'universe', s.universe,
                'benchmark', s.benchmark
            )
            ELSE jsonb_build_object(
                'id', sr_inner.strategy_id,
                'name', COALESCE(
                    sr_inner.metrics ->> 'strategy_name',
                    CASE
                        WHEN sr_inner.strategy_id IS NOT NULL THEN '已删除策略 #' || sr_inner.strategy_id::text
                        ELSE NULL
                    END
                ),
                'strategy_type', sr_inner.metrics ->> 'strategy_type',
                'params', COALESCE(sr_inner.metrics -> 'strategy_params', '{}'::jsonb),
                'universe', CASE
                    WHEN jsonb_typeof(sr_inner.metrics -> 'funds_used') = 'array'
                        THEN jsonb_build_object('fund_codes', sr_inner.metrics -> 'funds_used')
                    ELSE NULL
                END,
                'benchmark', NULL
            )
        END AS snapshot
    FROM simulation_runs sr_inner
    LEFT JOIN strategies s ON s.id = sr_inner.strategy_id
) AS sub
WHERE sr.id = sub.id
  AND sr.strategy_snapshot IS NULL;
"""


def _backfill_sqlite(connection) -> None:
    table_names = set(sa.inspect(connection).get_table_names())
    has_strategies = "strategies" in table_names

    if has_strategies:
        query = sa.text(
            """
            SELECT
                sr.id,
                sr.strategy_id,
                sr.metrics,
                s.name AS strategy_name,
                s.strategy_type,
                s.params AS strategy_params,
                s.universe AS strategy_universe,
                s.benchmark AS strategy_benchmark
            FROM simulation_runs sr
            LEFT JOIN strategies s ON s.id = sr.strategy_id
            WHERE sr.strategy_snapshot IS NULL
            ORDER BY sr.id
            """
        )
    else:
        query = sa.text(
            """
            SELECT
                sr.id,
                sr.strategy_id,
                sr.metrics,
                NULL AS strategy_name,
                NULL AS strategy_type,
                NULL AS strategy_params,
                NULL AS strategy_universe,
                NULL AS strategy_benchmark
            FROM simulation_runs sr
            WHERE sr.strategy_snapshot IS NULL
            ORDER BY sr.id
            """
        )

    rows = connection.execute(query).mappings().all()

    for row in rows:
        snapshot = _build_snapshot_from_row(dict(row))
        connection.execute(
            sa.text(
                "UPDATE simulation_runs SET strategy_snapshot = :snapshot WHERE id = :id"
            ),
            {"id": row["id"], "snapshot": json.dumps(snapshot, ensure_ascii=False)},
        )


def upgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name
    json_type = postgresql.JSONB(astext_type=sa.Text()) if dialect_name == "postgresql" else sa.JSON()

    op.add_column(
        "simulation_runs",
        sa.Column(
            "strategy_snapshot",
            json_type,
            nullable=True,
            comment="Strategy snapshot captured at simulation submission time",
        ),
    )

    if dialect_name == "postgresql":
        op.execute(sa.text(POSTGRES_SNAPSHOT_SQL))
    else:
        _backfill_sqlite(bind)


def downgrade() -> None:
    op.drop_column("simulation_runs", "strategy_snapshot")
