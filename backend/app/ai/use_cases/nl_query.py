"""Natural language query use case — two-stage approach.

Translates user natural language queries into structured database queries
using a two-stage pipeline:

1. **Intent extraction (LLM)**: The LLM parses the user's natural language
   input and produces a structured query intent (IR — Intermediate
   Representation). This IR is validated against a strict JSON Schema.

2. **SQL generation (code)**: Deterministic code converts the validated IR
   into a safe, read-only SQL query. This avoids SQL injection and
   hallucination risks inherent in letting LLMs generate SQL directly.

Supported intents:
- search_funds: Search/filter funds by various criteria
- get_fund_nav: Retrieve NAV history for a specific fund
- get_fund_factors: Retrieve computed factor values for a fund
- compare_funds: Compare multiple funds on specified metrics

Security:
- Write operations (INSERT/UPDATE/DELETE) are NEVER generated
- All SQL is parameterized
- Intent schema strictly limits allowed operations

Requirements: 11.13, 11.14
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Intent types
# ---------------------------------------------------------------------------


class QueryIntent(str, Enum):
    """Supported query intent types."""

    SEARCH_FUNDS = "search_funds"
    GET_FUND_NAV = "get_fund_nav"
    GET_FUND_FACTORS = "get_fund_factors"
    COMPARE_FUNDS = "compare_funds"


# ---------------------------------------------------------------------------
# JSON Schema for LLM output (Intent IR)
# ---------------------------------------------------------------------------

NL_QUERY_INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [i.value for i in QueryIntent],
            "description": "The type of query the user wants to perform",
        },
        "filters": {
            "type": "object",
            "description": "Filter conditions for the query",
            "properties": {
                "fund_code": {
                    "type": ["string", "null"],
                    "description": "Specific fund code (e.g. '000001')",
                },
                "fund_name": {
                    "type": ["string", "null"],
                    "description": "Fund name keyword for fuzzy search",
                },
                "fund_type": {
                    "type": ["string", "null"],
                    "enum": [
                        "stock", "bond", "mixed", "money",
                        "qdii", "fof", "index", None,
                    ],
                    "description": "Fund type filter",
                },
                "min_nav": {
                    "type": ["number", "null"],
                    "description": "Minimum NAV filter",
                },
                "max_nav": {
                    "type": ["number", "null"],
                    "description": "Maximum NAV filter",
                },
                "start_date": {
                    "type": ["string", "null"],
                    "description": "Start date for time range (YYYY-MM-DD)",
                },
                "end_date": {
                    "type": ["string", "null"],
                    "description": "End date for time range (YYYY-MM-DD)",
                },
                "company": {
                    "type": ["string", "null"],
                    "description": "Fund company name keyword",
                },
                "manager": {
                    "type": ["string", "null"],
                    "description": "Fund manager name keyword",
                },
                "fund_codes": {
                    "type": ["array", "null"],
                    "items": {"type": "string"},
                    "description": "List of fund codes for comparison",
                },
            },
            "additionalProperties": False,
        },
        "sort_by": {
            "type": ["string", "null"],
            "enum": [
                "sharpe", "return", "volatility", "max_drawdown",
                "nav", "inception_date", "fund_size", None,
            ],
            "description": "Field to sort results by",
        },
        "sort_order": {
            "type": "string",
            "enum": ["asc", "desc"],
            "default": "desc",
            "description": "Sort direction",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 20,
            "description": "Maximum number of results to return",
        },
        "metrics": {
            "type": ["array", "null"],
            "items": {
                "type": "string",
                "enum": [
                    "sharpe", "return", "volatility", "max_drawdown",
                    "beta", "alpha", "information_ratio", "sortino",
                    "calmar", "nav",
                ],
            },
            "description": "Metrics to include in comparison results",
        },
    },
    "required": ["intent"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是一个基金数据查询助手。你的任务是将用户的自然语言查询转换为结构化的查询意图。

支持的查询意图：
1. search_funds: 搜索/筛选基金（按名称、类型、规模、经理等条件）
2. get_fund_nav: 获取特定基金的净值历史
3. get_fund_factors: 获取特定基金的量化因子数据
4. compare_funds: 对比多只基金的指标

输出规则：
- intent: 必须是上述4种之一
- filters: 根据用户描述提取的筛选条件
- sort_by: 排序字段（如有）
- sort_order: 排序方向，默认 desc
- limit: 返回数量限制，默认 20，最大 100
- metrics: 对比时需要的指标列表

基金类型映射：
- 股票型/股票基金 → stock
- 债券型/债券基金 → bond
- 混合型/混合基金 → mixed
- 货币型/货币基金 → money
- QDII → qdii
- FOF/基金中基金 → fof
- 指数型/指数基金 → index

重要限制：
- 你只能生成只读查询意图，不能生成任何写入、修改或删除操作
- 日期格式必须为 YYYY-MM-DD
- 基金代码为6位数字字符串
- 如果用户的请求涉及修改数据、交易操作等非查询行为，将 intent 设为最接近的查询意图并忽略写入部分"""

USER_PROMPT_TEMPLATE = """请将以下自然语言查询转换为结构化查询意图（JSON格式）：

用户查询：{query}

请严格按照JSON Schema输出结果。"""


# ---------------------------------------------------------------------------
# SQL generation (deterministic, code-based)
# ---------------------------------------------------------------------------

# Allowed columns for sorting to prevent injection
_SORT_COLUMN_MAP: dict[str, str] = {
    "sharpe": "sharpe_ratio",
    "return": "annualized_return",
    "volatility": "volatility",
    "max_drawdown": "max_drawdown",
    "nav": "unit_nav",
    "inception_date": "inception_date",
    "fund_size": "fund_size",
}

# Write operation keywords that must be rejected
_WRITE_KEYWORDS = frozenset({
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
    "TRUNCATE", "REPLACE", "MERGE", "GRANT", "REVOKE",
})


@dataclass
class GeneratedQuery:
    """A generated SQL query with parameters.

    Attributes:
        sql: The parameterized SQL string (read-only SELECT).
        params: Dictionary of parameter values for safe binding.
        intent: The original query intent type.
    """

    sql: str
    params: dict[str, Any] = field(default_factory=dict)
    intent: QueryIntent = QueryIntent.SEARCH_FUNDS


class WriteOperationError(Exception):
    """Raised when a write operation is detected and rejected."""

    def __init__(self, message: str = "Write operations are not allowed") -> None:
        super().__init__(message)


class InvalidIntentError(Exception):
    """Raised when the intent IR is invalid or cannot be processed."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


def _validate_no_write_operations(intent_ir: dict[str, Any]) -> None:
    """Check that the intent IR does not contain write operation indicators.

    This is a defense-in-depth check. The schema already restricts intents
    to read-only types, but we double-check all string values.

    Raises:
        WriteOperationError: If any write operation indicator is found.
    """
    # Check all string values recursively
    def _check_value(val: Any) -> None:
        if isinstance(val, str):
            upper = val.upper()
            for keyword in _WRITE_KEYWORDS:
                if keyword in upper:
                    raise WriteOperationError(
                        f"Detected write operation keyword '{keyword}' in query intent"
                    )
        elif isinstance(val, dict):
            for v in val.values():
                _check_value(v)
        elif isinstance(val, list):
            for item in val:
                _check_value(item)

    _check_value(intent_ir)


def generate_sql_from_intent(intent_ir: dict[str, Any]) -> GeneratedQuery:
    """Convert a validated intent IR into a parameterized SQL query.

    This function is purely deterministic — no LLM involvement. It maps
    the structured intent to safe, parameterized SQL.

    Args:
        intent_ir: The validated intent intermediate representation.

    Returns:
        GeneratedQuery with parameterized SQL and bind parameters.

    Raises:
        WriteOperationError: If write operations are detected.
        InvalidIntentError: If the intent cannot be processed.
    """
    # Defense-in-depth: reject any write operation indicators
    _validate_no_write_operations(intent_ir)

    intent_str = intent_ir.get("intent")
    if not intent_str:
        raise InvalidIntentError("Missing 'intent' field")

    try:
        intent = QueryIntent(intent_str)
    except ValueError:
        raise InvalidIntentError(f"Unknown intent: {intent_str}")

    filters = intent_ir.get("filters") or {}
    sort_by = intent_ir.get("sort_by")
    sort_order = intent_ir.get("sort_order", "desc").upper()
    limit = intent_ir.get("limit", 20)

    # Validate sort_order
    if sort_order not in ("ASC", "DESC"):
        sort_order = "DESC"

    # Validate limit
    if not isinstance(limit, int) or limit < 1:
        limit = 20
    limit = min(limit, 100)

    if intent == QueryIntent.SEARCH_FUNDS:
        return _build_search_funds_query(filters, sort_by, sort_order, limit)
    elif intent == QueryIntent.GET_FUND_NAV:
        return _build_get_fund_nav_query(filters, sort_order, limit)
    elif intent == QueryIntent.GET_FUND_FACTORS:
        return _build_get_fund_factors_query(filters, limit)
    elif intent == QueryIntent.COMPARE_FUNDS:
        return _build_compare_funds_query(filters, intent_ir.get("metrics"))
    else:
        raise InvalidIntentError(f"Unhandled intent: {intent}")


def _build_search_funds_query(
    filters: dict[str, Any],
    sort_by: str | None,
    sort_order: str,
    limit: int,
) -> GeneratedQuery:
    """Build a search funds query."""
    conditions: list[str] = []
    params: dict[str, Any] = {}

    if filters.get("fund_code"):
        conditions.append("f.code = :fund_code")
        params["fund_code"] = filters["fund_code"]

    if filters.get("fund_name"):
        conditions.append("f.name ILIKE :fund_name")
        params["fund_name"] = f"%{filters['fund_name']}%"

    if filters.get("fund_type"):
        conditions.append("f.fund_type = :fund_type")
        params["fund_type"] = filters["fund_type"]

    if filters.get("company"):
        conditions.append("f.company_id ILIKE :company")
        params["company"] = f"%{filters['company']}%"

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    # Safe sort column mapping
    sort_column = "f.code"
    if sort_by and sort_by in _SORT_COLUMN_MAP:
        sort_column = _SORT_COLUMN_MAP[sort_by]

    sql = (
        f"SELECT f.code, f.name, f.fund_type, f.inception_date, f.status "
        f"FROM funds f "
        f"WHERE {where_clause} "
        f"ORDER BY {sort_column} {sort_order} "
        f"LIMIT :limit"
    )
    params["limit"] = limit

    return GeneratedQuery(sql=sql, params=params, intent=QueryIntent.SEARCH_FUNDS)


def _build_get_fund_nav_query(
    filters: dict[str, Any],
    sort_order: str,
    limit: int,
) -> GeneratedQuery:
    """Build a fund NAV history query."""
    fund_code = filters.get("fund_code")
    if not fund_code:
        raise InvalidIntentError("get_fund_nav requires a fund_code filter")

    conditions = ["fn.fund_code = :fund_code"]
    params: dict[str, Any] = {"fund_code": fund_code}

    if filters.get("start_date"):
        conditions.append("fn.trade_date >= :start_date")
        params["start_date"] = filters["start_date"]

    if filters.get("end_date"):
        conditions.append("fn.trade_date <= :end_date")
        params["end_date"] = filters["end_date"]

    where_clause = " AND ".join(conditions)

    sql = (
        f"SELECT fn.fund_code, fn.trade_date, fn.unit_nav, fn.accum_nav, "
        f"fn.adj_nav, fn.daily_return "
        f"FROM fund_nav fn "
        f"WHERE {where_clause} "
        f"ORDER BY fn.trade_date {sort_order} "
        f"LIMIT :limit"
    )
    params["limit"] = limit

    return GeneratedQuery(sql=sql, params=params, intent=QueryIntent.GET_FUND_NAV)


def _build_get_fund_factors_query(
    filters: dict[str, Any],
    limit: int,
) -> GeneratedQuery:
    """Build a fund factors query."""
    fund_code = filters.get("fund_code")
    if not fund_code:
        raise InvalidIntentError("get_fund_factors requires a fund_code filter")

    params: dict[str, Any] = {"fund_code": fund_code, "limit": limit}

    sql = (
        "SELECT f.code, f.name, f.fund_type "
        "FROM funds f "
        "WHERE f.code = :fund_code "
        "LIMIT :limit"
    )

    return GeneratedQuery(sql=sql, params=params, intent=QueryIntent.GET_FUND_FACTORS)


def _build_compare_funds_query(
    filters: dict[str, Any],
    metrics: list[str] | None,
) -> GeneratedQuery:
    """Build a fund comparison query."""
    fund_codes = filters.get("fund_codes")
    if not fund_codes or not isinstance(fund_codes, list) or len(fund_codes) < 2:
        raise InvalidIntentError(
            "compare_funds requires at least 2 fund codes in filters.fund_codes"
        )

    # Limit to 10 funds for comparison
    fund_codes = fund_codes[:10]
    params: dict[str, Any] = {"fund_codes": fund_codes}

    sql = (
        "SELECT f.code, f.name, f.fund_type, f.inception_date "
        "FROM funds f "
        "WHERE f.code = ANY(:fund_codes) "
        "ORDER BY f.code"
    )

    return GeneratedQuery(sql=sql, params=params, intent=QueryIntent.COMPARE_FUNDS)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class NLQueryResult:
    """Result of a natural language query.

    Attributes:
        intent: The parsed query intent.
        intent_ir: The full intermediate representation from LLM.
        generated_query: The generated SQL query (if successful).
        error: Error message if the query could not be processed.
        rejected: Whether the query was rejected (e.g. write operation).
        rejection_reason: Reason for rejection.
    """

    intent: QueryIntent | None = None
    intent_ir: dict[str, Any] = field(default_factory=dict)
    generated_query: GeneratedQuery | None = None
    error: str | None = None
    rejected: bool = False
    rejection_reason: str | None = None


# ---------------------------------------------------------------------------
# Main use case class
# ---------------------------------------------------------------------------


class NLQueryProcessor:
    """Processes natural language queries using a two-stage pipeline.

    Stage 1: LLM extracts a structured query intent (IR) from the
    user's natural language input.

    Stage 2: Deterministic code generates a safe, parameterized SQL
    query from the IR. No LLM is involved in SQL generation.

    Args:
        llm_service: The unified LLMService instance.
    """

    USE_CASE = "nl_query"

    def __init__(self, llm_service: Any) -> None:
        self._llm = llm_service

    async def process(self, query: str) -> NLQueryResult:
        """Process a natural language query.

        Args:
            query: The user's natural language query string.

        Returns:
            NLQueryResult with the parsed intent and generated query,
            or error/rejection information.
        """
        if not query or not query.strip():
            return NLQueryResult(
                error="Query cannot be empty",
            )

        # --- Stage 1: LLM intent extraction ---
        prompt = USER_PROMPT_TEMPLATE.format(query=query.strip())

        try:
            from app.ai.service import LLMResult

            llm_result: LLMResult = await self._llm.call(
                use_case=self.USE_CASE,
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                schema=NL_QUERY_INTENT_SCHEMA,
                temperature=0.1,
                max_tokens=500,
            )
        except Exception as exc:
            log.error("nl_query.llm_call_failed", error=str(exc))
            return NLQueryResult(error=f"LLM call failed: {exc}")

        # Extract intent IR
        intent_ir: dict[str, Any]
        if isinstance(llm_result.content, dict):
            intent_ir = llm_result.content
        else:
            log.warning(
                "nl_query.unexpected_content_type",
                content_type=type(llm_result.content).__name__,
            )
            return NLQueryResult(error="LLM returned unexpected content type")

        # --- Stage 2: Code-based SQL generation ---
        try:
            _validate_no_write_operations(intent_ir)
        except WriteOperationError as exc:
            log.warning("nl_query.write_operation_rejected", query=query)
            return NLQueryResult(
                intent_ir=intent_ir,
                rejected=True,
                rejection_reason=str(exc),
            )

        try:
            generated = generate_sql_from_intent(intent_ir)
        except (InvalidIntentError, WriteOperationError) as exc:
            if isinstance(exc, WriteOperationError):
                return NLQueryResult(
                    intent_ir=intent_ir,
                    rejected=True,
                    rejection_reason=str(exc),
                )
            return NLQueryResult(
                intent_ir=intent_ir,
                error=str(exc),
            )

        # Parse intent enum
        intent_str = intent_ir.get("intent", "")
        try:
            intent = QueryIntent(intent_str)
        except ValueError:
            intent = None

        log.info(
            "nl_query.success",
            intent=intent_str,
            query_preview=query[:50],
        )

        return NLQueryResult(
            intent=intent,
            intent_ir=intent_ir,
            generated_query=generated,
        )


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "GeneratedQuery",
    "InvalidIntentError",
    "NL_QUERY_INTENT_SCHEMA",
    "NLQueryProcessor",
    "NLQueryResult",
    "QueryIntent",
    "SYSTEM_PROMPT",
    "USER_PROMPT_TEMPLATE",
    "WriteOperationError",
    "generate_sql_from_intent",
]
