"""Tests for natural language query use case (task 8.1).

Covers:
- Intent schema definition correctness
- SQL generation from intent IR (all 4 intent types)
- Write operation rejection
- NLQueryProcessor integration with mock LLMService
- API endpoint integration tests
- Edge cases: empty query, invalid intent, missing required fields

Requirements: 11.13, 11.14
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.service import LLMResult
from app.ai.use_cases.nl_query import (
    NL_QUERY_INTENT_SCHEMA,
    GeneratedQuery,
    InvalidIntentError,
    NLQueryProcessor,
    NLQueryResult,
    QueryIntent,
    WriteOperationError,
    generate_sql_from_intent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_llm_result(content: dict[str, Any]) -> LLMResult:
    """Create a mock LLMResult with dict content."""
    return LLMResult(
        content=content,
        raw_response=None,
        provider="test",
        model="test-model",
        cached=False,
        cost_usd=0.001,
        latency_ms=100,
    )


def _make_mock_llm_service(response_content: dict[str, Any]) -> AsyncMock:
    """Create a mock LLMService that returns the given content."""
    service = AsyncMock()
    service.call = AsyncMock(return_value=_make_llm_result(response_content))
    return service


# ---------------------------------------------------------------------------
# Tests: Schema definition
# ---------------------------------------------------------------------------


class TestIntentSchema:
    """Test the NL query intent JSON Schema definition."""

    def test_schema_requires_intent(self):
        """Schema must require 'intent' field."""
        assert "required" in NL_QUERY_INTENT_SCHEMA
        assert "intent" in NL_QUERY_INTENT_SCHEMA["required"]

    def test_schema_intent_enum(self):
        """Intent enum must contain all 4 supported types."""
        intent_enum = NL_QUERY_INTENT_SCHEMA["properties"]["intent"]["enum"]
        expected = ["search_funds", "get_fund_nav", "get_fund_factors", "compare_funds"]
        assert set(intent_enum) == set(expected)

    def test_schema_sort_by_enum(self):
        """sort_by should have valid enum values."""
        sort_enum = NL_QUERY_INTENT_SCHEMA["properties"]["sort_by"]["enum"]
        assert "sharpe" in sort_enum
        assert "return" in sort_enum
        assert "nav" in sort_enum
        assert None in sort_enum  # nullable

    def test_schema_limit_bounds(self):
        """limit should be bounded between 1 and 100."""
        limit_prop = NL_QUERY_INTENT_SCHEMA["properties"]["limit"]
        assert limit_prop["minimum"] == 1
        assert limit_prop["maximum"] == 100

    def test_schema_no_additional_properties(self):
        """Schema should not allow additional properties at top level."""
        assert NL_QUERY_INTENT_SCHEMA.get("additionalProperties") is False

    def test_schema_filters_no_additional_properties(self):
        """Filters should not allow additional properties."""
        filters_prop = NL_QUERY_INTENT_SCHEMA["properties"]["filters"]
        assert filters_prop.get("additionalProperties") is False


# ---------------------------------------------------------------------------
# Tests: SQL generation — search_funds
# ---------------------------------------------------------------------------


class TestSearchFundsSQL:
    """Test SQL generation for search_funds intent."""

    def test_basic_search(self):
        """Basic search with no filters should produce valid SQL."""
        intent_ir = {"intent": "search_funds"}
        result = generate_sql_from_intent(intent_ir)

        assert result.intent == QueryIntent.SEARCH_FUNDS
        assert "SELECT" in result.sql
        assert "FROM funds" in result.sql
        assert "LIMIT" in result.sql
        assert result.params["limit"] == 20

    def test_search_by_fund_type(self):
        """Search with fund_type filter."""
        intent_ir = {
            "intent": "search_funds",
            "filters": {"fund_type": "stock"},
        }
        result = generate_sql_from_intent(intent_ir)

        assert "fund_type = :fund_type" in result.sql
        assert result.params["fund_type"] == "stock"

    def test_search_by_name(self):
        """Search with fund_name filter uses ILIKE."""
        intent_ir = {
            "intent": "search_funds",
            "filters": {"fund_name": "华夏"},
        }
        result = generate_sql_from_intent(intent_ir)

        assert "ILIKE :fund_name" in result.sql
        assert result.params["fund_name"] == "%华夏%"

    def test_search_by_code(self):
        """Search with specific fund_code."""
        intent_ir = {
            "intent": "search_funds",
            "filters": {"fund_code": "000001"},
        }
        result = generate_sql_from_intent(intent_ir)

        assert "code = :fund_code" in result.sql
        assert result.params["fund_code"] == "000001"

    def test_search_with_sort(self):
        """Search with sort_by should use mapped column."""
        intent_ir = {
            "intent": "search_funds",
            "sort_by": "sharpe",
            "sort_order": "desc",
        }
        result = generate_sql_from_intent(intent_ir)

        assert "ORDER BY sharpe_ratio DESC" in result.sql

    def test_search_with_limit(self):
        """Custom limit should be respected."""
        intent_ir = {
            "intent": "search_funds",
            "limit": 5,
        }
        result = generate_sql_from_intent(intent_ir)

        assert result.params["limit"] == 5

    def test_search_limit_capped_at_100(self):
        """Limit should be capped at 100."""
        intent_ir = {
            "intent": "search_funds",
            "limit": 500,
        }
        result = generate_sql_from_intent(intent_ir)

        assert result.params["limit"] == 100

    def test_search_by_company(self):
        """Search with company filter."""
        intent_ir = {
            "intent": "search_funds",
            "filters": {"company": "易方达"},
        }
        result = generate_sql_from_intent(intent_ir)

        assert "company_id ILIKE :company" in result.sql
        assert result.params["company"] == "%易方达%"


# ---------------------------------------------------------------------------
# Tests: SQL generation — get_fund_nav
# ---------------------------------------------------------------------------


class TestGetFundNavSQL:
    """Test SQL generation for get_fund_nav intent."""

    def test_basic_nav_query(self):
        """Basic NAV query with fund_code."""
        intent_ir = {
            "intent": "get_fund_nav",
            "filters": {"fund_code": "000001"},
        }
        result = generate_sql_from_intent(intent_ir)

        assert result.intent == QueryIntent.GET_FUND_NAV
        assert "fund_nav" in result.sql
        assert "fund_code = :fund_code" in result.sql
        assert result.params["fund_code"] == "000001"

    def test_nav_with_date_range(self):
        """NAV query with date range filters."""
        intent_ir = {
            "intent": "get_fund_nav",
            "filters": {
                "fund_code": "000001",
                "start_date": "2024-01-01",
                "end_date": "2024-06-30",
            },
        }
        result = generate_sql_from_intent(intent_ir)

        assert "trade_date >= :start_date" in result.sql
        assert "trade_date <= :end_date" in result.sql
        assert result.params["start_date"] == "2024-01-01"
        assert result.params["end_date"] == "2024-06-30"

    def test_nav_without_fund_code_raises(self):
        """NAV query without fund_code should raise InvalidIntentError."""
        intent_ir = {
            "intent": "get_fund_nav",
            "filters": {},
        }
        with pytest.raises(InvalidIntentError, match="fund_code"):
            generate_sql_from_intent(intent_ir)

    def test_nav_includes_all_nav_fields(self):
        """NAV query should select unit_nav, accum_nav, adj_nav."""
        intent_ir = {
            "intent": "get_fund_nav",
            "filters": {"fund_code": "000001"},
        }
        result = generate_sql_from_intent(intent_ir)

        assert "unit_nav" in result.sql
        assert "accum_nav" in result.sql
        assert "adj_nav" in result.sql


# ---------------------------------------------------------------------------
# Tests: SQL generation — get_fund_factors
# ---------------------------------------------------------------------------


class TestGetFundFactorsSQL:
    """Test SQL generation for get_fund_factors intent."""

    def test_basic_factors_query(self):
        """Basic factors query with fund_code."""
        intent_ir = {
            "intent": "get_fund_factors",
            "filters": {"fund_code": "110011"},
        }
        result = generate_sql_from_intent(intent_ir)

        assert result.intent == QueryIntent.GET_FUND_FACTORS
        assert result.params["fund_code"] == "110011"

    def test_factors_without_fund_code_raises(self):
        """Factors query without fund_code should raise."""
        intent_ir = {
            "intent": "get_fund_factors",
            "filters": {},
        }
        with pytest.raises(InvalidIntentError, match="fund_code"):
            generate_sql_from_intent(intent_ir)


# ---------------------------------------------------------------------------
# Tests: SQL generation — compare_funds
# ---------------------------------------------------------------------------


class TestCompareFundsSQL:
    """Test SQL generation for compare_funds intent."""

    def test_basic_comparison(self):
        """Basic comparison with multiple fund codes."""
        intent_ir = {
            "intent": "compare_funds",
            "filters": {"fund_codes": ["000001", "000002", "000003"]},
            "metrics": ["sharpe", "return"],
        }
        result = generate_sql_from_intent(intent_ir)

        assert result.intent == QueryIntent.COMPARE_FUNDS
        assert "ANY(:fund_codes)" in result.sql
        assert result.params["fund_codes"] == ["000001", "000002", "000003"]

    def test_comparison_requires_at_least_2_funds(self):
        """Comparison with less than 2 funds should raise."""
        intent_ir = {
            "intent": "compare_funds",
            "filters": {"fund_codes": ["000001"]},
        }
        with pytest.raises(InvalidIntentError, match="at least 2"):
            generate_sql_from_intent(intent_ir)

    def test_comparison_without_fund_codes_raises(self):
        """Comparison without fund_codes should raise."""
        intent_ir = {
            "intent": "compare_funds",
            "filters": {},
        }
        with pytest.raises(InvalidIntentError, match="at least 2"):
            generate_sql_from_intent(intent_ir)

    def test_comparison_caps_at_10_funds(self):
        """Comparison should cap at 10 funds."""
        codes = [f"00000{i}" for i in range(15)]
        intent_ir = {
            "intent": "compare_funds",
            "filters": {"fund_codes": codes},
        }
        result = generate_sql_from_intent(intent_ir)

        assert len(result.params["fund_codes"]) == 10


# ---------------------------------------------------------------------------
# Tests: Write operation rejection
# ---------------------------------------------------------------------------


class TestWriteOperationRejection:
    """Test that write operations are always rejected."""

    def test_reject_insert_keyword(self):
        """Intent containing INSERT keyword should be rejected."""
        intent_ir = {
            "intent": "search_funds",
            "filters": {"fund_name": "INSERT INTO funds"},
        }
        with pytest.raises(WriteOperationError):
            generate_sql_from_intent(intent_ir)

    def test_reject_update_keyword(self):
        """Intent containing UPDATE keyword should be rejected."""
        intent_ir = {
            "intent": "search_funds",
            "filters": {"fund_name": "UPDATE funds SET"},
        }
        with pytest.raises(WriteOperationError):
            generate_sql_from_intent(intent_ir)

    def test_reject_delete_keyword(self):
        """Intent containing DELETE keyword should be rejected."""
        intent_ir = {
            "intent": "search_funds",
            "filters": {"fund_name": "DELETE FROM funds"},
        }
        with pytest.raises(WriteOperationError):
            generate_sql_from_intent(intent_ir)

    def test_reject_drop_keyword(self):
        """Intent containing DROP keyword should be rejected."""
        intent_ir = {
            "intent": "search_funds",
            "filters": {"fund_name": "DROP TABLE funds"},
        }
        with pytest.raises(WriteOperationError):
            generate_sql_from_intent(intent_ir)

    def test_reject_truncate_keyword(self):
        """Intent containing TRUNCATE keyword should be rejected."""
        intent_ir = {
            "intent": "search_funds",
            "filters": {"fund_name": "TRUNCATE funds"},
        }
        with pytest.raises(WriteOperationError):
            generate_sql_from_intent(intent_ir)

    def test_reject_case_insensitive(self):
        """Write keyword detection should be case-insensitive."""
        intent_ir = {
            "intent": "search_funds",
            "filters": {"fund_name": "insert into funds"},
        }
        with pytest.raises(WriteOperationError):
            generate_sql_from_intent(intent_ir)

    def test_reject_nested_write_keyword(self):
        """Write keywords in nested structures should be detected."""
        intent_ir = {
            "intent": "compare_funds",
            "filters": {"fund_codes": ["000001", "DELETE"]},
        }
        with pytest.raises(WriteOperationError):
            generate_sql_from_intent(intent_ir)

    def test_normal_query_not_rejected(self):
        """Normal queries without write keywords should pass."""
        intent_ir = {
            "intent": "search_funds",
            "filters": {"fund_name": "华夏成长"},
        }
        # Should not raise
        result = generate_sql_from_intent(intent_ir)
        assert result is not None


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases in SQL generation."""

    def test_unknown_intent_raises(self):
        """Unknown intent should raise InvalidIntentError."""
        intent_ir = {"intent": "unknown_intent"}
        with pytest.raises(InvalidIntentError, match="Unknown intent"):
            generate_sql_from_intent(intent_ir)

    def test_missing_intent_raises(self):
        """Missing intent field should raise InvalidIntentError."""
        intent_ir = {"filters": {}}
        with pytest.raises(InvalidIntentError, match="Missing"):
            generate_sql_from_intent(intent_ir)

    def test_invalid_sort_order_defaults_to_desc(self):
        """Invalid sort_order should default to DESC."""
        intent_ir = {
            "intent": "search_funds",
            "sort_order": "invalid",
        }
        result = generate_sql_from_intent(intent_ir)
        assert "DESC" in result.sql

    def test_invalid_limit_defaults_to_20(self):
        """Invalid limit should default to 20."""
        intent_ir = {
            "intent": "search_funds",
            "limit": -5,
        }
        result = generate_sql_from_intent(intent_ir)
        assert result.params["limit"] == 20

    def test_unknown_sort_by_uses_default(self):
        """Unknown sort_by should use default sort column."""
        intent_ir = {
            "intent": "search_funds",
            "sort_by": "unknown_field",
        }
        result = generate_sql_from_intent(intent_ir)
        # Should use default f.code
        assert "ORDER BY f.code" in result.sql

    def test_empty_filters(self):
        """Empty filters should produce a valid query."""
        intent_ir = {
            "intent": "search_funds",
            "filters": {},
        }
        result = generate_sql_from_intent(intent_ir)
        assert "1=1" in result.sql


# ---------------------------------------------------------------------------
# Tests: NLQueryProcessor
# ---------------------------------------------------------------------------


class TestNLQueryProcessor:
    """Test the NLQueryProcessor class with mock LLMService."""

    @pytest.mark.asyncio
    async def test_process_search_funds(self):
        """Should process a search funds query successfully."""
        llm_response = {
            "intent": "search_funds",
            "filters": {"fund_type": "stock"},
            "sort_by": "sharpe",
            "sort_order": "desc",
            "limit": 10,
        }
        mock_service = _make_mock_llm_service(llm_response)
        processor = NLQueryProcessor(mock_service)

        result = await processor.process("查找夏普比率最高的股票型基金")

        assert result.intent == QueryIntent.SEARCH_FUNDS
        assert result.generated_query is not None
        assert result.error is None
        assert result.rejected is False
        assert "fund_type = :fund_type" in result.generated_query.sql

    @pytest.mark.asyncio
    async def test_process_get_fund_nav(self):
        """Should process a NAV query successfully."""
        llm_response = {
            "intent": "get_fund_nav",
            "filters": {
                "fund_code": "000001",
                "start_date": "2024-01-01",
            },
        }
        mock_service = _make_mock_llm_service(llm_response)
        processor = NLQueryProcessor(mock_service)

        result = await processor.process("查看000001基金2024年以来的净值")

        assert result.intent == QueryIntent.GET_FUND_NAV
        assert result.generated_query is not None
        assert result.generated_query.params["fund_code"] == "000001"

    @pytest.mark.asyncio
    async def test_process_compare_funds(self):
        """Should process a comparison query successfully."""
        llm_response = {
            "intent": "compare_funds",
            "filters": {"fund_codes": ["000001", "000002"]},
            "metrics": ["sharpe", "return"],
        }
        mock_service = _make_mock_llm_service(llm_response)
        processor = NLQueryProcessor(mock_service)

        result = await processor.process("对比000001和000002的夏普比率")

        assert result.intent == QueryIntent.COMPARE_FUNDS
        assert result.generated_query is not None

    @pytest.mark.asyncio
    async def test_process_empty_query(self):
        """Empty query should return error."""
        mock_service = _make_mock_llm_service({})
        processor = NLQueryProcessor(mock_service)

        result = await processor.process("")

        assert result.error is not None
        assert "empty" in result.error.lower()

    @pytest.mark.asyncio
    async def test_process_whitespace_only_query(self):
        """Whitespace-only query should return error."""
        mock_service = _make_mock_llm_service({})
        processor = NLQueryProcessor(mock_service)

        result = await processor.process("   ")

        assert result.error is not None

    @pytest.mark.asyncio
    async def test_process_rejects_write_operations(self):
        """Should reject queries with write operation keywords."""
        llm_response = {
            "intent": "search_funds",
            "filters": {"fund_name": "DELETE FROM funds"},
        }
        mock_service = _make_mock_llm_service(llm_response)
        processor = NLQueryProcessor(mock_service)

        result = await processor.process("删除所有基金数据")

        assert result.rejected is True
        assert result.rejection_reason is not None

    @pytest.mark.asyncio
    async def test_process_llm_failure(self):
        """Should handle LLM call failure gracefully."""
        mock_service = AsyncMock()
        mock_service.call = AsyncMock(side_effect=Exception("LLM timeout"))
        processor = NLQueryProcessor(mock_service)

        result = await processor.process("查找基金")

        assert result.error is not None
        assert "failed" in result.error.lower()

    @pytest.mark.asyncio
    async def test_process_non_dict_llm_response(self):
        """Should handle non-dict LLM response gracefully."""
        mock_service = AsyncMock()
        mock_service.call = AsyncMock(
            return_value=LLMResult(
                content="unexpected string response",
                raw_response=None,
                provider="test",
                model="test",
                cached=False,
                cost_usd=0.0,
                latency_ms=0,
            )
        )
        processor = NLQueryProcessor(mock_service)

        result = await processor.process("查找基金")

        assert result.error is not None
        assert "unexpected" in result.error.lower()

    @pytest.mark.asyncio
    async def test_process_invalid_intent_from_llm(self):
        """Should handle invalid intent from LLM."""
        llm_response = {
            "intent": "invalid_intent_type",
            "filters": {},
        }
        mock_service = _make_mock_llm_service(llm_response)
        processor = NLQueryProcessor(mock_service)

        result = await processor.process("做一些奇怪的事情")

        assert result.error is not None

    @pytest.mark.asyncio
    async def test_llm_service_called_with_correct_params(self):
        """Verify LLMService is called with correct use_case and schema."""
        llm_response = {
            "intent": "search_funds",
            "filters": {},
        }
        mock_service = _make_mock_llm_service(llm_response)
        processor = NLQueryProcessor(mock_service)

        await processor.process("查找基金")

        mock_service.call.assert_called_once()
        call_kwargs = mock_service.call.call_args[1]
        assert call_kwargs["use_case"] == "nl_query"
        assert call_kwargs["schema"] == NL_QUERY_INTENT_SCHEMA
        assert call_kwargs["temperature"] == 0.1

    @pytest.mark.asyncio
    async def test_process_get_fund_factors(self):
        """Should process a factors query successfully."""
        llm_response = {
            "intent": "get_fund_factors",
            "filters": {"fund_code": "110011"},
        }
        mock_service = _make_mock_llm_service(llm_response)
        processor = NLQueryProcessor(mock_service)

        result = await processor.process("查看110011的量化因子")

        assert result.intent == QueryIntent.GET_FUND_FACTORS
        assert result.generated_query is not None


# ---------------------------------------------------------------------------
# Tests: API endpoint integration
# ---------------------------------------------------------------------------


class TestNLQueryAPI:
    """Test the POST /ai/query API endpoint."""

    @pytest.fixture
    def client(self):
        """Create a test client with mocked dependencies."""
        from fastapi.testclient import TestClient

        from app.main import create_app
        from app.core.config import Settings

        settings = Settings(
            app_env="test",
            database_url="sqlite+aiosqlite:///test.db",
            redis_url="redis://localhost:6379/0",
        )
        app = create_app(settings)
        return TestClient(app)

    @pytest.fixture
    def mock_llm_service(self):
        """Create a mock LLM service."""
        return _make_mock_llm_service({
            "intent": "search_funds",
            "filters": {"fund_type": "stock"},
            "sort_by": "sharpe",
            "sort_order": "desc",
            "limit": 10,
        })

    def test_endpoint_exists(self, client):
        """The /ai/query endpoint should exist (even if deps fail)."""
        response = client.post(
            "/api/v1/ai/query",
            json={"query": "查找基金"},
        )
        # May return 503 if AI service not configured, but not 404
        assert response.status_code != 404

    def test_endpoint_rejects_empty_query(self, client):
        """Empty query should return 422 validation error."""
        response = client.post(
            "/api/v1/ai/query",
            json={"query": ""},
        )
        assert response.status_code == 422

    def test_endpoint_rejects_missing_query(self, client):
        """Missing query field should return 422."""
        response = client.post(
            "/api/v1/ai/query",
            json={},
        )
        assert response.status_code == 422

    def test_endpoint_rejects_too_long_query(self, client):
        """Query exceeding max_length should return 422."""
        response = client.post(
            "/api/v1/ai/query",
            json={"query": "x" * 501},
        )
        assert response.status_code == 422
