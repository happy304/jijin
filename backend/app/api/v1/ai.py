"""AI-related API endpoints.

Provides endpoints for AI-assisted features including factor brainstorm,
natural language queries, strategy generation, attribution reports,
and usage/cost dashboard.

When the global config ``AI_ENABLED`` is False, all endpoints under this
router return HTTP 501 Not Implemented. Core platform functionality
remains unaffected.

Requirements: 11.6, 11.13-11.26
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.ai.feature_gate import require_ai_enabled
from app.core.logging import get_logger

log = get_logger(__name__)

router = APIRouter(
    prefix="/ai",
    tags=["ai"],
    dependencies=[Depends(require_ai_enabled)],
)


# ---------------------------------------------------------------------------
# Usage / Cost Dashboard — Response models
# ---------------------------------------------------------------------------


class ProviderUsage(BaseModel):
    """Per-provider usage breakdown."""

    calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class UseCaseUsage(BaseModel):
    """Per-use-case usage breakdown."""

    calls: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class BudgetStatus(BaseModel):
    """Current budget status from Redis counters."""

    daily_spend_usd: float
    daily_limit_usd: float
    daily_remaining_usd: float
    monthly_spend_usd: float
    monthly_limit_usd: float
    monthly_remaining_usd: float
    date: str
    month: str


class AIUsageResponse(BaseModel):
    """Response model for the AI usage dashboard endpoint."""

    total_calls: int = Field(description="Total LLM calls in the period")
    successful_calls: int = Field(description="Successful calls")
    failed_calls: int = Field(description="Failed calls")
    total_prompt_tokens: int = Field(description="Total prompt tokens consumed")
    total_completion_tokens: int = Field(description="Total completion tokens")
    total_tokens: int = Field(description="Total tokens (prompt + completion)")
    total_cost_usd: float = Field(description="Total estimated cost in USD")
    avg_latency_ms: float | None = Field(description="Average latency in ms")
    by_provider: dict[str, ProviderUsage] = Field(
        default_factory=dict, description="Breakdown by provider"
    )
    by_use_case: dict[str, UseCaseUsage] = Field(
        default_factory=dict, description="Breakdown by use case"
    )
    budget: BudgetStatus | None = Field(
        None, description="Current budget status"
    )
    period_start: str = Field(description="Start of reporting period (ISO)")
    period_end: str = Field(description="End of reporting period (ISO)")


# ---------------------------------------------------------------------------
# Usage endpoint
# ---------------------------------------------------------------------------


@router.get(
    "/usage",
    response_model=AIUsageResponse,
    summary="AI usage and cost dashboard",
    description=(
        "Returns aggregated AI/LLM usage statistics for the specified period "
        "(default: last 30 days), including call counts, token consumption, "
        "cost estimates, and current budget status."
    ),
    responses={
        200: {"description": "Usage statistics retrieved successfully"},
    },
)
async def get_ai_usage(
    days: int = Query(default=30, ge=1, le=365, description="Number of days to look back"),
) -> AIUsageResponse:
    """Get AI usage statistics and budget status.

    Combines data from:
    - LLMAuditLog.get_stats(): historical call statistics from the database
    - LLMBudget.get_usage(): current daily/monthly budget counters from Redis

    Requirements: 11.6
    """
    from app.ai.audit import LLMAuditLog
    from app.ai.budget import LLMBudget

    audit_log = LLMAuditLog()
    budget = LLMBudget()

    try:
        # Fetch audit stats (from database)
        stats = await audit_log.get_stats(days=days)

        # Fetch budget status (from Redis)
        try:
            budget_usage = await budget.get_usage()
            budget_status = BudgetStatus(**budget_usage)
        except Exception as exc:
            log.warning("ai.usage.budget_fetch_error", error=str(exc))
            budget_status = None
        finally:
            await budget.close()

        return AIUsageResponse(
            total_calls=stats["total_calls"],
            successful_calls=stats["successful_calls"],
            failed_calls=stats["failed_calls"],
            total_prompt_tokens=stats["total_prompt_tokens"],
            total_completion_tokens=stats["total_completion_tokens"],
            total_tokens=stats["total_tokens"],
            total_cost_usd=stats["total_cost_usd"],
            avg_latency_ms=stats["avg_latency_ms"],
            by_provider={
                k: ProviderUsage(**v) for k, v in stats["by_provider"].items()
            },
            by_use_case={
                k: UseCaseUsage(**v) for k, v in stats["by_use_case"].items()
            },
            budget=budget_status,
            period_start=stats["period_start"],
            period_end=stats["period_end"],
        )
    except Exception as exc:
        log.error("ai.usage.error", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve AI usage statistics",
        ) from exc


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class FactorBrainstormRequest(BaseModel):
    """Request body for the factor brainstorm endpoint."""

    hypothesis: str = Field(
        ...,
        min_length=5,
        max_length=2000,
        description="Research idea or hypothesis to generate candidate factors from",
        examples=["动量因子在小盘基金中可能有更强的预测力"],
    )


class CandidateFactorResponse(BaseModel):
    """A single candidate factor in the response."""

    name: str
    formula: str
    rationale: str
    is_valid_dsl: bool
    dsl_errors: list[str] = Field(default_factory=list)
    ic_ir_result: dict[str, Any] | None = None
    registered: bool = False


class FactorBrainstormResponse(BaseModel):
    """Response body for the factor brainstorm endpoint."""

    hypothesis: str
    candidates: list[CandidateFactorResponse]
    valid_count: int
    significant_count: int
    registered_count: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/factor-brainstorm",
    response_model=FactorBrainstormResponse,
    summary="Generate candidate factors from a research hypothesis",
    description=(
        "Uses LLM to generate candidate factor formulas in a restricted DSL "
        "based on the user's research hypothesis. Validates DSL expressions "
        "and optionally submits for IC/IR testing."
    ),
)
async def factor_brainstorm(
    request: FactorBrainstormRequest,
) -> FactorBrainstormResponse:
    """Generate candidate factors from a research hypothesis.

    The endpoint:
    1. Sends the hypothesis to LLM for factor formula generation
    2. Validates each formula against the restricted DSL
    3. Optionally runs IC/IR significance testing
    4. Returns all candidates with validation status

    Requirements: 11.20, 11.21, 11.22, 11.23
    """
    from app.ai.service import AllProvidersFailedError, BudgetExhaustedError
    from app.ai.use_cases.factor_brainstorm import FactorBrainstormer

    # In production, these would be injected via dependency injection
    # For now, we create a minimal instance that requires LLMService setup
    try:
        # Get LLM service from app state (would be set up during startup)
        from app.ai.use_cases.factor_brainstorm import FactorBrainstormer

        # This is a simplified version — in production, the LLMService
        # would be injected via FastAPI dependencies
        raise HTTPException(
            status_code=501,
            detail=(
                "Factor brainstorm endpoint requires LLMService configuration. "
                "Set up AI providers in environment variables."
            ),
        )
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="AI module not available",
        )


# ---------------------------------------------------------------------------
# Factory function for creating a configured endpoint
# ---------------------------------------------------------------------------


def create_factor_brainstorm_endpoint(llm_service: Any) -> None:
    """Create a fully configured factor brainstorm endpoint.

    This factory is called during app startup when LLMService is available.
    It replaces the placeholder endpoint with a functional one.

    Args:
        llm_service: Configured LLMService instance.
    """
    from app.ai.use_cases.factor_brainstorm import FactorBrainstormer

    brainstormer = FactorBrainstormer(llm_service)

    @router.post(
        "/factor-brainstorm",
        response_model=FactorBrainstormResponse,
        summary="Generate candidate factors from a research hypothesis",
        include_in_schema=False,  # Avoid duplicate in OpenAPI
    )
    async def _factor_brainstorm_configured(
        request: FactorBrainstormRequest,
    ) -> FactorBrainstormResponse:
        from app.ai.service import AllProvidersFailedError, BudgetExhaustedError

        try:
            result = await brainstormer.brainstorm(request.hypothesis)
        except BudgetExhaustedError:
            raise HTTPException(
                status_code=429,
                detail="AI budget exhausted. Please try again later.",
            )
        except AllProvidersFailedError as e:
            raise HTTPException(
                status_code=503,
                detail=f"All AI providers failed: {e}",
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        candidates = [
            CandidateFactorResponse(
                name=c.name,
                formula=c.formula,
                rationale=c.rationale,
                is_valid_dsl=c.is_valid_dsl,
                dsl_errors=c.dsl_errors,
                ic_ir_result=(
                    {
                        "ic_mean": c.ic_ir_result.ic_mean,
                        "ic_std": c.ic_ir_result.ic_std,
                        "ir": c.ic_ir_result.ir,
                        "significance": c.ic_ir_result.significance.value,
                        "p_value": c.ic_ir_result.p_value,
                    }
                    if c.ic_ir_result
                    else None
                ),
                registered=c.registered,
            )
            for c in result.candidates
        ]

        return FactorBrainstormResponse(
            hypothesis=result.hypothesis,
            candidates=candidates,
            valid_count=result.valid_count,
            significant_count=result.significant_count,
            registered_count=result.registered_count,
        )


# ---------------------------------------------------------------------------
# NL Query — Request / Response models
# ---------------------------------------------------------------------------


class NLQueryRequest(BaseModel):
    """Request body for the natural language query endpoint."""

    query: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Natural language query string",
        examples=["查找夏普比率最高的股票型基金"],
    )


class GeneratedQueryResponse(BaseModel):
    """Generated SQL query details."""

    sql: str
    params: dict[str, Any]


class NLQueryResponse(BaseModel):
    """Response from the natural language query endpoint."""

    intent: str | None = Field(
        None, description="Parsed query intent type"
    )
    intent_ir: dict[str, Any] = Field(
        default_factory=dict, description="Full intent intermediate representation"
    )
    generated_query: GeneratedQueryResponse | None = Field(
        None, description="Generated SQL query (if successful)"
    )
    error: str | None = Field(
        None, description="Error message if processing failed"
    )
    rejected: bool = Field(
        False, description="Whether the query was rejected (e.g. write operation)"
    )
    rejection_reason: str | None = Field(
        None, description="Reason for rejection"
    )


# ---------------------------------------------------------------------------
# NL Query endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/query",
    response_model=NLQueryResponse,
    summary="Natural language query",
    description=(
        "Translates a natural language query into a structured database query "
        "using a two-stage pipeline: LLM intent extraction → code-based SQL "
        "generation. Write operations are rejected."
    ),
    responses={
        200: {"description": "Query processed successfully"},
        400: {"description": "Invalid query input"},
        403: {"description": "Write operation rejected"},
        503: {"description": "AI service unavailable"},
    },
)
async def nl_query(request: NLQueryRequest) -> NLQueryResponse:
    """Process a natural language query.

    Two-stage pipeline:
    1. LLM extracts structured query intent (IR) from natural language
    2. Deterministic code generates safe, parameterized SQL from the IR

    Write operations (INSERT/UPDATE/DELETE) are always rejected.

    Requirements: 11.13, 11.14
    """
    from app.ai.use_cases.nl_query import NLQueryProcessor

    # Get LLM service from default providers
    try:
        from app.ai.providers import build_default_providers
        from app.ai.service import LLMService

        providers = build_default_providers()
        if not providers:
            raise HTTPException(
                status_code=503,
                detail="AI service is not configured — no LLM providers available",
            )
        llm_service = LLMService(providers=providers)
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="AI service is not configured or unavailable",
        )

    processor = NLQueryProcessor(llm_service)
    result = await processor.process(request.query)

    # Handle rejection (write operations)
    if result.rejected:
        raise HTTPException(
            status_code=403,
            detail=result.rejection_reason or "Query rejected: write operations not allowed",
        )

    # Handle errors
    if result.error:
        raise HTTPException(
            status_code=400,
            detail=result.error,
        )

    # Build response
    generated = None
    if result.generated_query:
        generated = GeneratedQueryResponse(
            sql=result.generated_query.sql,
            params=result.generated_query.params,
        )

    return NLQueryResponse(
        intent=result.intent.value if result.intent else None,
        intent_ir=result.intent_ir,
        generated_query=generated,
        error=result.error,
        rejected=result.rejected,
        rejection_reason=result.rejection_reason,
    )


# ---------------------------------------------------------------------------
# Strategy Generation — Request / Response models
# ---------------------------------------------------------------------------


class StrategyGenRequest(BaseModel):
    """Request body for natural language strategy generation."""

    description: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="自然语言策略描述",
        examples=["帮我做一个动量轮动策略，每月从5只基金中选表现最好的3只"],
    )


class StrategyGenResponse(BaseModel):
    """Response model for strategy generation."""

    strategy_type: str = Field(..., description="生成的策略类型")
    name: str = Field(default="", description="策略名称")
    params: dict[str, Any] = Field(default_factory=dict, description="策略参数")
    universe: dict[str, Any] = Field(default_factory=dict, description="基金池配置")
    reasoning: str = Field(default="", description="选择理由")
    is_valid: bool = Field(..., description="配置是否通过校验")
    validation_errors: list[str] = Field(
        default_factory=list, description="校验错误列表"
    )


# ---------------------------------------------------------------------------
# Strategy Generation endpoint
# ---------------------------------------------------------------------------


def _get_strategy_generator():
    """Build a StrategyGenerator instance.

    In production this would use a proper dependency injection pattern.
    For now, we construct the LLMService from app config.
    """
    from app.ai.providers import build_default_providers
    from app.ai.service import LLMService
    from app.ai.use_cases.strategy_gen import StrategyGenerator

    providers = build_default_providers()
    llm_service = LLMService(providers=providers)
    return StrategyGenerator(llm_service=llm_service)


@router.post(
    "/strategy-gen",
    response_model=StrategyGenResponse,
    summary="自然语言生成策略配置",
    description=(
        "根据用户的自然语言描述，使用 LLM 生成策略配置 JSON。"
        "生成的配置经过 Schema 校验和参数范围检查，用户确认后可保存为策略。"
    ),
    responses={
        200: {"description": "策略配置生成成功"},
        429: {"description": "AI 调用预算已耗尽"},
        503: {"description": "AI 服务不可用"},
    },
)
async def generate_strategy(body: StrategyGenRequest) -> StrategyGenResponse:
    """Generate a strategy configuration from natural language description.

    The endpoint:
    1. Sends the description to LLM with strategy template context
    2. Validates the generated config against the strategy's parameter schema
    3. Checks parameter ranges
    4. Returns the validated config for user confirmation

    Requirements: 11.15, 11.16
    """
    from app.ai.service import AllProvidersFailedError, BudgetExhaustedError

    try:
        generator = _get_strategy_generator()
    except Exception as exc:
        log.error("ai.strategy_gen.init_failed", error=str(exc))
        raise HTTPException(
            status_code=503,
            detail="AI 服务暂不可用，请检查 LLM 配置",
        ) from exc

    try:
        result = await generator.generate(body.description)
    except BudgetExhaustedError:
        raise HTTPException(
            status_code=429,
            detail="AI 调用预算已耗尽，请稍后再试",
        )
    except AllProvidersFailedError as exc:
        log.error("ai.strategy_gen.all_providers_failed", errors=exc.errors)
        raise HTTPException(
            status_code=503,
            detail="AI 服务暂时不可用，所有 LLM 提供商均失败",
        )
    except Exception as exc:
        log.error("ai.strategy_gen.unexpected_error", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="策略生成过程中发生内部错误",
        ) from exc

    return StrategyGenResponse(
        strategy_type=result.strategy_type,
        name=result.name,
        params=result.params,
        universe=result.universe,
        reasoning=result.reasoning,
        is_valid=result.is_valid,
        validation_errors=result.validation_errors,
    )


# ---------------------------------------------------------------------------
# Attribution Report — Request / Response models
# ---------------------------------------------------------------------------


class AttributionReportRequest(BaseModel):
    """Request body for the AI attribution report endpoint."""

    run_id: int = Field(..., description="回测运行 ID")


class AttributionReportResponse(BaseModel):
    """Response model for the AI attribution report."""

    report_text: str = Field(..., description="AI 生成的分析报告")
    ai_generated_label: str = Field(..., description="AI 生成内容标签")
    data_link: str = Field(default="", description="原始数据链接")
    input_data: dict[str, Any] = Field(default_factory=dict, description="输入的原始数据")


# ---------------------------------------------------------------------------
# Attribution Report endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/attribution-report",
    response_model=AttributionReportResponse,
    summary="AI 智能归因报告",
    description=(
        "基于已计算的归因数据和绩效指标，使用 LLM 生成自然语言分析报告。"
        "LLM 仅负责解释数据，不做任何计算。"
    ),
    responses={
        200: {"description": "报告生成成功"},
        400: {"description": "回测尚未完成"},
        404: {"description": "回测不存在"},
        429: {"description": "AI 调用预算已耗尽"},
        503: {"description": "AI 服务不可用"},
    },
)
async def generate_attribution_report(
    body: AttributionReportRequest,
) -> AttributionReportResponse:
    """Generate an AI attribution report for a completed backtest run.

    Requirements: 11.17, 11.18, 11.19
    """
    from sqlalchemy import select

    from app.ai.service import AllProvidersFailedError, BudgetExhaustedError
    from app.ai.use_cases.attribution_report import (
        AttributionReportGenerator,
        AttributionReportInput,
    )
    from app.data.models.backtests import BacktestRun
    from app.data.session import get_sessionmaker

    # Load backtest run from database
    factory = get_sessionmaker()
    async with factory() as db:
        result = await db.execute(
            select(BacktestRun).where(BacktestRun.id == body.run_id)
        )
        run = result.scalar_one_or_none()

        if run is None:
            raise HTTPException(status_code=404, detail=f"回测 {body.run_id} 不存在")

        if run.status != "done":
            raise HTTPException(
                status_code=400,
                detail=f"回测尚未完成，当前状态: {run.status}",
            )

        metrics = run.metrics or {}

        # Get strategy name
        strategy_name = "未命名策略"
        if run.strategy_id:
            from app.data.models.strategies import Strategy

            strat_result = await db.execute(
                select(Strategy).where(Strategy.id == run.strategy_id)
            )
            strat = strat_result.scalar_one_or_none()
            if strat:
                strategy_name = strat.name

    # Build input data from metrics
    input_data = AttributionReportInput(
        strategy_name=strategy_name,
        run_id=str(body.run_id),
        return_metrics={
            k: metrics.get(k)
            for k in ["total_return", "annualized_return", "cagr"]
            if metrics.get(k) is not None
        },
        risk_metrics={
            k: metrics.get(k)
            for k in ["volatility", "max_drawdown", "var_95", "cvar_95"]
            if metrics.get(k) is not None
        },
        risk_adjusted_metrics={
            k: metrics.get(k)
            for k in ["sharpe_ratio", "sortino_ratio", "calmar_ratio", "information_ratio"]
            if metrics.get(k) is not None
        },
        benchmark_metrics={
            k: metrics.get(k)
            for k in ["alpha", "beta", "tracking_error", "benchmark_return"]
            if metrics.get(k) is not None
        },
        fama_french=metrics.get("fama_french"),
        brinson=metrics.get("brinson"),
    )

    # Build LLM service and generate report
    try:
        from app.ai.providers import build_default_providers
        from app.ai.service import LLMService

        providers = build_default_providers()
        if not providers:
            raise HTTPException(
                status_code=503,
                detail="AI 服务暂不可用，未配置 LLM 提供商",
            )
        llm_service = LLMService(providers=providers)
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="AI 模块不可用",
        )

    generator = AttributionReportGenerator(llm_service)

    try:
        report = await generator.generate(input_data)
    except BudgetExhaustedError:
        raise HTTPException(
            status_code=429,
            detail="AI 调用预算已耗尽，请稍后再试",
        )
    except AllProvidersFailedError as exc:
        log.error("ai.attribution_report.all_providers_failed", errors=str(exc))
        raise HTTPException(
            status_code=503,
            detail="AI 服务暂时不可用，所有 LLM 提供商均失败",
        )
    except Exception as exc:
        log.error("ai.attribution_report.unexpected_error", error=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"报告生成过程中发生内部错误: {str(exc)}",
        ) from exc

    return AttributionReportResponse(
        report_text=report.report_text,
        ai_generated_label=report.ai_generated_label,
        data_link=report.data_link,
        input_data=report.input_data,
    )


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "AIUsageResponse",
    "AttributionReportRequest",
    "AttributionReportResponse",
    "BudgetStatus",
    "CandidateFactorResponse",
    "FactorBrainstormRequest",
    "FactorBrainstormResponse",
    "NLQueryRequest",
    "NLQueryResponse",
    "ProviderUsage",
    "StrategyGenRequest",
    "StrategyGenResponse",
    "UseCaseUsage",
    "create_factor_brainstorm_endpoint",
    "router",
]
