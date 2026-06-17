"""LLM audit logging — records every LLM call to the ``llm_calls`` table.

All calls to cloud LLM providers are persisted for cost tracking,
debugging, and compliance (requirement 11.5). The :class:`LLMAuditLog`
class provides:

* :meth:`record` — insert a single audit row after each LLM call.
* :meth:`get_stats` — aggregate statistics over a configurable window
  (default: last 30 days).

Design notes
------------
* Uses SQLAlchemy async sessions for non-blocking database writes.
* Graceful degradation: audit failures are logged but never propagate
  to the caller. A database outage must not break the LLM pipeline.
* Statistics queries use SQL aggregation for efficiency rather than
  loading all rows into Python.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.data.models.llm_calls import LLMCall

log = get_logger(__name__)


class LLMAuditLog:
    """Audit logger that persists LLM call records to the database.

    Args:
        session_factory: An async sessionmaker that produces
            :class:`AsyncSession` instances. If not provided, the
            module-level factory from ``app.data.session`` is used.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._session_factory = session_factory

    def _get_session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Return the session factory, importing lazily if needed."""
        if self._session_factory is None:
            from app.data.session import get_sessionmaker

            self._session_factory = get_sessionmaker()
        return self._session_factory

    async def record(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        use_case: str | None = None,
        prompt_hash: str | None = None,
        prompt_text: str | None = None,
        response_text: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cost_usd: float | None = None,
        latency_ms: int | None = None,
        success: bool | None = None,
        error_msg: str | None = None,
    ) -> None:
        """Record a single LLM call to the audit table.

        All parameters are optional to allow partial logging (e.g. when
        a call fails before receiving token counts).

        This method never raises — errors are logged and swallowed so
        the LLM pipeline is not disrupted by audit failures.
        """
        try:
            factory = self._get_session_factory()
            async with factory() as session:
                record = LLMCall(
                    provider=provider,
                    model=model,
                    use_case=use_case,
                    prompt_hash=prompt_hash,
                    prompt_text=prompt_text,
                    response_text=response_text,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    success=success,
                    error_msg=error_msg,
                )
                session.add(record)
                await session.commit()
                log.debug(
                    "llm_audit.recorded",
                    provider=provider,
                    model=model,
                    use_case=use_case,
                    success=success,
                )
        except Exception as exc:
            log.warning(
                "llm_audit.record.error",
                provider=provider,
                model=model,
                use_case=use_case,
                error=str(exc),
            )

    async def get_stats(self, days: int = 30) -> dict:
        """Return aggregate LLM usage statistics for the last N days.

        Args:
            days: Number of days to look back (default 30).

        Returns:
            A dictionary containing:
            - total_calls: Total number of LLM calls
            - successful_calls: Number of successful calls
            - failed_calls: Number of failed calls
            - total_prompt_tokens: Sum of prompt tokens
            - total_completion_tokens: Sum of completion tokens
            - total_tokens: Sum of all tokens
            - total_cost_usd: Total estimated cost in USD
            - avg_latency_ms: Average latency in milliseconds
            - by_provider: Dict of per-provider stats
            - by_use_case: Dict of per-use-case stats
            - period_start: Start of the reporting period (ISO string)
            - period_end: End of the reporting period (ISO string)
        """
        try:
            factory = self._get_session_factory()
            since = datetime.now(timezone.utc) - timedelta(days=days)

            async with factory() as session:
                # Overall aggregates
                overall_stmt = select(
                    func.count(LLMCall.id).label("total_calls"),
                    func.count(LLMCall.id).filter(LLMCall.success.is_(True)).label(
                        "successful_calls"
                    ),
                    func.count(LLMCall.id).filter(LLMCall.success.is_(False)).label(
                        "failed_calls"
                    ),
                    func.coalesce(func.sum(LLMCall.prompt_tokens), 0).label(
                        "total_prompt_tokens"
                    ),
                    func.coalesce(func.sum(LLMCall.completion_tokens), 0).label(
                        "total_completion_tokens"
                    ),
                    func.coalesce(
                        func.sum(LLMCall.prompt_tokens)
                        + func.sum(LLMCall.completion_tokens),
                        0,
                    ).label("total_tokens"),
                    func.coalesce(func.sum(LLMCall.cost_usd), 0).label(
                        "total_cost_usd"
                    ),
                    func.avg(LLMCall.latency_ms).label("avg_latency_ms"),
                ).where(LLMCall.created_at >= since)

                result = await session.execute(overall_stmt)
                row = result.one()

                # Per-provider breakdown
                provider_stmt = (
                    select(
                        LLMCall.provider,
                        func.count(LLMCall.id).label("calls"),
                        func.coalesce(func.sum(LLMCall.prompt_tokens), 0).label(
                            "prompt_tokens"
                        ),
                        func.coalesce(func.sum(LLMCall.completion_tokens), 0).label(
                            "completion_tokens"
                        ),
                        func.coalesce(func.sum(LLMCall.cost_usd), 0).label("cost_usd"),
                    )
                    .where(LLMCall.created_at >= since)
                    .group_by(LLMCall.provider)
                )
                provider_result = await session.execute(provider_stmt)
                by_provider = {
                    prow.provider or "unknown": {
                        "calls": prow.calls,
                        "prompt_tokens": int(prow.prompt_tokens),
                        "completion_tokens": int(prow.completion_tokens),
                        "cost_usd": float(prow.cost_usd),
                    }
                    for prow in provider_result.all()
                }

                # Per-use-case breakdown
                use_case_stmt = (
                    select(
                        LLMCall.use_case,
                        func.count(LLMCall.id).label("calls"),
                        func.coalesce(func.sum(LLMCall.prompt_tokens), 0).label(
                            "prompt_tokens"
                        ),
                        func.coalesce(func.sum(LLMCall.completion_tokens), 0).label(
                            "completion_tokens"
                        ),
                        func.coalesce(func.sum(LLMCall.cost_usd), 0).label("cost_usd"),
                    )
                    .where(LLMCall.created_at >= since)
                    .group_by(LLMCall.use_case)
                )
                use_case_result = await session.execute(use_case_stmt)
                by_use_case = {
                    ucrow.use_case or "unknown": {
                        "calls": ucrow.calls,
                        "prompt_tokens": int(ucrow.prompt_tokens),
                        "completion_tokens": int(ucrow.completion_tokens),
                        "cost_usd": float(ucrow.cost_usd),
                    }
                    for ucrow in use_case_result.all()
                }

            # Convert Decimal values to float for JSON serialization
            total_cost = row.total_cost_usd
            if isinstance(total_cost, Decimal):
                total_cost = float(total_cost)
            else:
                total_cost = float(total_cost) if total_cost else 0.0

            avg_latency = row.avg_latency_ms
            if avg_latency is not None:
                avg_latency = round(float(avg_latency), 1)

            return {
                "total_calls": row.total_calls,
                "successful_calls": row.successful_calls,
                "failed_calls": row.failed_calls,
                "total_prompt_tokens": int(row.total_prompt_tokens),
                "total_completion_tokens": int(row.total_completion_tokens),
                "total_tokens": int(row.total_tokens),
                "total_cost_usd": round(total_cost, 6),
                "avg_latency_ms": avg_latency,
                "by_provider": by_provider,
                "by_use_case": by_use_case,
                "period_start": since.isoformat(),
                "period_end": datetime.now(timezone.utc).isoformat(),
            }

        except Exception as exc:
            log.warning("llm_audit.get_stats.error", error=str(exc))
            return {
                "total_calls": 0,
                "successful_calls": 0,
                "failed_calls": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "avg_latency_ms": None,
                "by_provider": {},
                "by_use_case": {},
                "period_start": (
                    datetime.now(timezone.utc) - timedelta(days=days)
                ).isoformat(),
                "period_end": datetime.now(timezone.utc).isoformat(),
            }


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = ["LLMAuditLog"]
