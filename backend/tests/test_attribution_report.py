"""Tests for the attribution report use case (task 8.3).

Covers:
- AttributionReportGenerator with mock LLMService
- Prompt construction verifies no-compute constraint
- Output includes AI generated label and data link
- Edge cases: empty data, missing fields, partial attribution

Requirements: 11.17, 11.18, 11.19
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.ai.service import LLMResult
from app.ai.use_cases.attribution_report import (
    AI_GENERATED_LABEL,
    DATA_LINK_TEMPLATE,
    SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    AttributionReportGenerator,
    AttributionReportInput,
    AttributionReportOutput,
    _format_metrics,
    _format_value,
)


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------


def _make_llm_result(content: str) -> LLMResult:
    """Create a mock LLMResult with text content."""
    return LLMResult(
        content=content,
        raw_response=None,
        provider="test-provider",
        model="test-model",
        cached=False,
        cost_usd=0.002,
        latency_ms=150,
    )


def _make_mock_llm_service(response_text: str) -> AsyncMock:
    """Create a mock LLMService that returns the given text."""
    service = AsyncMock()
    service.call = AsyncMock(return_value=_make_llm_result(response_text))
    return service


def _sample_input() -> AttributionReportInput:
    """Create a sample input with realistic data."""
    return AttributionReportInput(
        strategy_name="动量轮动策略",
        run_id="123",
        return_metrics={
            "total_return": 0.2534,
            "annualized_return": 0.1205,
            "excess_return": 0.0523,
            "jensen_alpha": 0.0312,
        },
        risk_metrics={
            "volatility": 0.1856,
            "max_drawdown": -0.1234,
            "downside_deviation": 0.1102,
            "var_95": -0.0189,
            "cvar_95": -0.0267,
            "calmar": 0.9765,
        },
        risk_adjusted_metrics={
            "sharpe": 0.6498,
            "sortino": 1.0934,
            "information_ratio": 0.4521,
            "treynor": 0.0823,
        },
        benchmark_metrics={
            "beta": 0.8523,
            "tracking_error": 0.0756,
            "r_squared": 0.7234,
            "up_capture": 0.9123,
            "down_capture": 0.7856,
        },
        fama_french={
            "alpha": 0.0312,
            "betas": {"MKT": 0.85, "SMB": 0.12, "HML": -0.08},
            "r_squared": 0.72,
            "model_type": "3-factor",
        },
        brinson={
            "allocation_effect": {"金融": 0.005, "科技": 0.012, "total": 0.017},
            "selection_effect": {"金融": 0.003, "科技": 0.008, "total": 0.011},
            "interaction_effect": {"金融": 0.001, "科技": 0.002, "total": 0.003},
            "total_excess_return": 0.031,
        },
    )


# ---------------------------------------------------------------------------
# Tests: Prompt constraints
# ---------------------------------------------------------------------------


class TestPromptConstraints:
    """Verify that prompts explicitly forbid LLM from computing values."""

    def test_system_prompt_forbids_computation(self):
        """System prompt must explicitly forbid LLM from computing values."""
        assert "不要自行计算任何数值" in SYSTEM_PROMPT

    def test_system_prompt_forbids_speculation(self):
        """System prompt must forbid speculation on unprovided info."""
        assert "不要推测未提供的信息" in SYSTEM_PROMPT

    def test_system_prompt_forbids_investment_advice(self):
        """System prompt must forbid investment advice."""
        assert "不要给出投资建议" in SYSTEM_PROMPT

    def test_user_prompt_template_forbids_computation(self):
        """User prompt template must also forbid computation."""
        assert "不要自行计算任何数值" in USER_PROMPT_TEMPLATE

    def test_user_prompt_template_forbids_speculation(self):
        """User prompt template must forbid speculation."""
        assert "不要推测未提供的信息" in USER_PROMPT_TEMPLATE

    def test_system_prompt_requires_data_only(self):
        """System prompt must state to only explain given data."""
        assert "只解释已给数据" in SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Tests: AI generated label
# ---------------------------------------------------------------------------


class TestAILabel:
    """Verify AI generated content label is present."""

    def test_label_contains_ai_marker(self):
        """Label must indicate AI-generated content."""
        assert "AI 生成内容" in AI_GENERATED_LABEL

    def test_label_contains_disclaimer(self):
        """Label must contain a disclaimer."""
        assert "不构成投资建议" in AI_GENERATED_LABEL


# ---------------------------------------------------------------------------
# Tests: Data link
# ---------------------------------------------------------------------------


class TestDataLink:
    """Verify original data link template."""

    def test_data_link_template_has_run_id_placeholder(self):
        """Data link template must include run_id placeholder."""
        assert "{run_id}" in DATA_LINK_TEMPLATE

    def test_data_link_points_to_attribution_endpoint(self):
        """Data link should point to the attribution API endpoint."""
        assert "/attribution" in DATA_LINK_TEMPLATE


# ---------------------------------------------------------------------------
# Tests: AttributionReportGenerator
# ---------------------------------------------------------------------------


class TestAttributionReportGenerator:
    """Test the main generator class."""

    @pytest.mark.asyncio
    async def test_generate_returns_report_with_text(self):
        """Generator should return a report with LLM-generated text."""
        mock_service = _make_mock_llm_service(
            "该策略在回测期间表现良好，年化收益率为12.05%..."
        )
        generator = AttributionReportGenerator(mock_service)
        input_data = _sample_input()

        result = await generator.generate(input_data)

        assert isinstance(result, AttributionReportOutput)
        assert "年化收益率" in result.report_text
        assert len(result.report_text) > 0

    @pytest.mark.asyncio
    async def test_output_includes_ai_label(self):
        """Output must include the AI generated content label."""
        mock_service = _make_mock_llm_service("分析报告内容")
        generator = AttributionReportGenerator(mock_service)
        input_data = _sample_input()

        result = await generator.generate(input_data)

        assert result.ai_generated_label == AI_GENERATED_LABEL
        assert "AI 生成内容" in result.ai_generated_label

    @pytest.mark.asyncio
    async def test_output_includes_data_link(self):
        """Output must include the original data link."""
        mock_service = _make_mock_llm_service("分析报告内容")
        generator = AttributionReportGenerator(mock_service)
        input_data = _sample_input()

        result = await generator.generate(input_data)

        assert result.data_link == "/api/v1/backtests/123/attribution"

    @pytest.mark.asyncio
    async def test_output_includes_input_data(self):
        """Output must include the raw input data for reference."""
        mock_service = _make_mock_llm_service("分析报告内容")
        generator = AttributionReportGenerator(mock_service)
        input_data = _sample_input()

        result = await generator.generate(input_data)

        assert result.input_data["strategy_name"] == "动量轮动策略"
        assert result.input_data["return_metrics"]["total_return"] == 0.2534
        assert result.input_data["fama_french"] is not None
        assert result.input_data["brinson"] is not None

    @pytest.mark.asyncio
    async def test_llm_called_with_correct_use_case(self):
        """LLMService must be called with use_case='attribution_report'."""
        mock_service = _make_mock_llm_service("报告")
        generator = AttributionReportGenerator(mock_service)
        input_data = _sample_input()

        await generator.generate(input_data)

        mock_service.call.assert_called_once()
        call_kwargs = mock_service.call.call_args[1]
        assert call_kwargs["use_case"] == "attribution_report"

    @pytest.mark.asyncio
    async def test_llm_called_with_system_prompt(self):
        """LLMService must be called with the constraint system prompt."""
        mock_service = _make_mock_llm_service("报告")
        generator = AttributionReportGenerator(mock_service)
        input_data = _sample_input()

        await generator.generate(input_data)

        call_kwargs = mock_service.call.call_args[1]
        assert call_kwargs["system_prompt"] == SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_llm_called_without_schema(self):
        """LLMService must be called with schema=None (text output)."""
        mock_service = _make_mock_llm_service("报告")
        generator = AttributionReportGenerator(mock_service)
        input_data = _sample_input()

        await generator.generate(input_data)

        call_kwargs = mock_service.call.call_args[1]
        assert call_kwargs["schema"] is None

    @pytest.mark.asyncio
    async def test_prompt_contains_input_data(self):
        """The prompt sent to LLM must contain the input metrics."""
        mock_service = _make_mock_llm_service("报告")
        generator = AttributionReportGenerator(mock_service)
        input_data = _sample_input()

        await generator.generate(input_data)

        call_kwargs = mock_service.call.call_args[1]
        prompt = call_kwargs["prompt"]
        # Verify key data appears in the prompt
        assert "动量轮动策略" in prompt
        assert "total_return" in prompt
        assert "sharpe" in prompt
        assert "MKT" in prompt
        assert "allocation_effect" in prompt

    @pytest.mark.asyncio
    async def test_empty_run_id_produces_empty_data_link(self):
        """Empty run_id should produce empty data link."""
        mock_service = _make_mock_llm_service("报告")
        generator = AttributionReportGenerator(mock_service)
        input_data = AttributionReportInput(
            strategy_name="测试",
            run_id="",
            return_metrics={"total_return": 0.1},
        )

        result = await generator.generate(input_data)

        assert result.data_link == ""

    @pytest.mark.asyncio
    async def test_partial_data_no_fama_french(self):
        """Should handle missing Fama-French data gracefully."""
        mock_service = _make_mock_llm_service("部分数据报告")
        generator = AttributionReportGenerator(mock_service)
        input_data = AttributionReportInput(
            strategy_name="简单策略",
            run_id="456",
            return_metrics={"total_return": 0.15},
            risk_metrics={"max_drawdown": -0.08},
            fama_french=None,
            brinson=None,
        )

        result = await generator.generate(input_data)

        assert result.report_text == "部分数据报告"
        assert result.input_data["fama_french"] is None
        assert result.input_data["brinson"] is None

    @pytest.mark.asyncio
    async def test_to_dict_serialization(self):
        """Output to_dict should produce a JSON-serializable dict."""
        mock_service = _make_mock_llm_service("序列化测试报告")
        generator = AttributionReportGenerator(mock_service)
        input_data = _sample_input()

        result = await generator.generate(input_data)
        result_dict = result.to_dict()

        assert isinstance(result_dict, dict)
        assert "report_text" in result_dict
        assert "ai_generated_label" in result_dict
        assert "data_link" in result_dict
        assert "input_data" in result_dict
        assert result_dict["report_text"] == "序列化测试报告"

    @pytest.mark.asyncio
    async def test_only_accepts_precomputed_data(self):
        """Input must be pre-computed data, not raw nav series."""
        # This test verifies the design: input is dict of computed values,
        # not raw data that would require computation
        input_data = _sample_input()

        # All inputs are plain dicts with pre-computed numbers
        assert isinstance(input_data.return_metrics, dict)
        assert isinstance(input_data.risk_metrics, dict)
        assert isinstance(input_data.fama_french, dict)
        assert isinstance(input_data.brinson, dict)

        # Values are already computed floats, not Series or DataFrames
        assert isinstance(input_data.return_metrics["total_return"], float)
        assert isinstance(input_data.risk_metrics["max_drawdown"], float)


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_empty_metrics(self):
        """Should handle completely empty metrics."""
        mock_service = _make_mock_llm_service("无数据报告")
        generator = AttributionReportGenerator(mock_service)
        input_data = AttributionReportInput(
            strategy_name="空策略",
            run_id="789",
        )

        result = await generator.generate(input_data)

        assert result.report_text == "无数据报告"
        assert result.ai_generated_label == AI_GENERATED_LABEL

    @pytest.mark.asyncio
    async def test_none_values_in_metrics(self):
        """Should handle None values in metrics gracefully."""
        mock_service = _make_mock_llm_service("含空值报告")
        generator = AttributionReportGenerator(mock_service)
        input_data = AttributionReportInput(
            strategy_name="部分空值",
            run_id="101",
            return_metrics={"total_return": None, "annualized_return": 0.05},
            risk_metrics={"volatility": None},
        )

        result = await generator.generate(input_data)

        # Should not crash, prompt should contain "不可用" for None values
        assert result.report_text == "含空值报告"

    @pytest.mark.asyncio
    async def test_llm_returns_non_string(self):
        """Should handle non-string LLM response gracefully."""
        mock_service = AsyncMock()
        mock_service.call = AsyncMock(
            return_value=LLMResult(
                content={"unexpected": "dict"},
                raw_response=None,
                provider="test",
                model="test",
                cached=False,
                cost_usd=0.0,
                latency_ms=0,
            )
        )
        generator = AttributionReportGenerator(mock_service)
        input_data = _sample_input()

        result = await generator.generate(input_data)

        # Should convert to string without crashing
        assert isinstance(result.report_text, str)
        assert len(result.report_text) > 0


# ---------------------------------------------------------------------------
# Tests: Helper functions
# ---------------------------------------------------------------------------


class TestHelpers:
    """Test helper formatting functions."""

    def test_format_metrics_none(self):
        """None input should return '（无数据）'."""
        assert _format_metrics(None) == "（无数据）"

    def test_format_metrics_empty_dict(self):
        """Empty dict should return '（无数据）'."""
        assert _format_metrics({}) == "（无数据）"

    def test_format_metrics_simple(self):
        """Simple dict should format as key: value lines."""
        result = _format_metrics({"sharpe": 1.5, "sortino": 2.0})
        assert "sharpe: 1.500000" in result
        assert "sortino: 2.000000" in result

    def test_format_metrics_nested_dict(self):
        """Nested dict should be formatted inline."""
        result = _format_metrics({"betas": {"MKT": 0.85, "SMB": 0.12}})
        assert "betas:" in result
        assert "MKT: 0.850000" in result
        assert "SMB: 0.120000" in result

    def test_format_metrics_with_none_value(self):
        """None values should show as '不可用'."""
        result = _format_metrics({"alpha": None})
        assert "不可用" in result

    def test_format_value_float(self):
        """Float values should be formatted to 6 decimal places."""
        assert _format_value(0.123456789) == "0.123457"

    def test_format_value_none(self):
        """None should return '不可用'."""
        assert _format_value(None) == "不可用"

    def test_format_value_string(self):
        """String values should pass through."""
        assert _format_value("3-factor") == "3-factor"

    def test_format_value_int(self):
        """Int values should be converted to string."""
        assert _format_value(252) == "252"
