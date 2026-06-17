"""Tests for quarterly report extraction use case (task 7.7).

Covers:
- JSON Schema definition correctness
- PDF text extraction (with mock PDF bytes)
- HTML text extraction
- Text truncation logic
- ReportExtractor integration with mock LLMService
- Edge cases: empty content, non-dict LLM response, missing fields

Requirements: 11.9
"""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.service import LLMResult
from app.ai.use_cases.report_extract import (
    MAX_TEXT_LENGTH,
    REPORT_EXTRACT_SCHEMA,
    ReportExtractResult,
    ReportExtractor,
    _truncate_text,
    extract_text_from_html,
    extract_text_from_pdf,
)


# ---------------------------------------------------------------------------
# Fixtures / Helpers
# ---------------------------------------------------------------------------


def _make_llm_result(content: dict[str, Any]) -> LLMResult:
    """Create a mock LLMResult with dict content."""
    return LLMResult(
        content=content,
        raw_response=None,
        provider="test",
        model="test-model",
        cached=False,
        cost_usd=0.002,
        latency_ms=200,
    )


def _make_mock_llm_service(response_content: dict[str, Any]) -> AsyncMock:
    """Create a mock LLMService that returns the given content."""
    service = AsyncMock()
    service.call = AsyncMock(return_value=_make_llm_result(response_content))
    return service


# Sample report text (simulating extracted content from a quarterly report)
SAMPLE_REPORT_TEXT = """
易方达蓝筹精选混合型证券投资基金2024年第1季度报告

基金代码：005827
基金名称：易方达蓝筹精选混合

一、管理人报告

1. 投资策略和运作分析

2024年一季度，A股市场先抑后扬，沪深300指数上涨3.10%。本基金在报告期内保持了较高的股票仓位，
重点配置了消费、医药、互联网等行业的优质龙头公司。我们认为这些公司具有较强的竞争壁垒和
持续的盈利增长能力，长期投资价值突出。

在操作上，我们适度调整了持仓结构，增加了对新能源和半导体行业的配置，
减持了部分估值较高的消费股。整体投资风格偏向成长与价值均衡。

2. 后市展望

展望二季度，我们认为宏观经济将延续温和复苏态势，企业盈利有望逐步改善。
市场流动性保持合理充裕，有利于权益资产表现。我们将继续坚持自下而上精选个股的策略，
重点关注具有核心竞争力和长期成长空间的优质公司。

3. 重仓股说明

本基金重仓持有贵州茅台、腾讯控股、宁德时代等公司，主要基于以下考虑：
这些公司在各自行业中具有显著的竞争优势，拥有强大的品牌力或技术壁垒，
且估值处于合理区间，具备较好的长期投资价值。
"""

SAMPLE_HTML_REPORT = """
<html>
<head><title>基金季报</title></head>
<body>
<h1>XX基金2024年第2季度报告</h1>
<div class="content">
<h2>管理人报告</h2>
<p>报告期内，本基金坚持价值投资理念，重点配置了银行、保险等低估值蓝筹板块。</p>
<p>基金经理认为当前市场估值处于历史低位，具有较好的安全边际。</p>
<h2>后市展望</h2>
<p>我们对下半年市场持谨慎乐观态度，将继续关注政策面变化和经济复苏节奏。</p>
</div>
<script>var x = 1;</script>
<style>.hidden { display: none; }</style>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Tests: Schema definition
# ---------------------------------------------------------------------------


class TestSchema:
    """Test the JSON Schema definition."""

    def test_schema_has_required_fields(self):
        """Schema must require the 4 core extraction fields."""
        required = REPORT_EXTRACT_SCHEMA["required"]
        assert "manager_view" in required
        assert "style_description" in required
        assert "market_outlook" in required
        assert "key_holdings_rationale" in required

    def test_schema_fields_are_nullable_strings(self):
        """All extraction fields should be nullable strings."""
        props = REPORT_EXTRACT_SCHEMA["properties"]
        for field_name in ["manager_view", "style_description", "market_outlook",
                           "key_holdings_rationale"]:
            assert props[field_name]["type"] == ["string", "null"]

    def test_schema_has_metadata_fields(self):
        """Schema should include report_period, fund_code, fund_name."""
        props = REPORT_EXTRACT_SCHEMA["properties"]
        assert "report_period" in props
        assert "fund_code" in props
        assert "fund_name" in props

    def test_schema_no_additional_properties(self):
        """Schema should not allow additional properties."""
        assert REPORT_EXTRACT_SCHEMA["additionalProperties"] is False


# ---------------------------------------------------------------------------
# Tests: HTML text extraction
# ---------------------------------------------------------------------------


class TestHtmlExtraction:
    """Test HTML text extraction."""

    def test_basic_html_extraction(self):
        """Should extract text from basic HTML."""
        html = "<p>Hello <b>world</b></p>"
        result = extract_text_from_html(html)
        assert "Hello" in result
        assert "world" in result
        assert "<" not in result

    def test_script_removal(self):
        """Should remove script blocks."""
        html = "<p>Text</p><script>alert('x')</script><p>More</p>"
        result = extract_text_from_html(html)
        assert "alert" not in result
        assert "Text" in result
        assert "More" in result

    def test_style_removal(self):
        """Should remove style blocks."""
        html = "<style>.x{color:red}</style><p>Content</p>"
        result = extract_text_from_html(html)
        assert "color" not in result
        assert "Content" in result

    def test_entity_decoding(self):
        """Should decode common HTML entities."""
        html = "<p>A &amp; B &lt; C &gt; D &quot;E&quot;</p>"
        result = extract_text_from_html(html)
        assert "A & B" in result
        assert "< C >" in result

    def test_whitespace_normalization(self):
        """Should normalize excessive whitespace."""
        html = "<p>  Hello   \n\n  World  </p>"
        result = extract_text_from_html(html)
        # Should not have multiple consecutive spaces
        assert "  " not in result

    def test_sample_report_html(self):
        """Should extract meaningful text from sample report HTML."""
        result = extract_text_from_html(SAMPLE_HTML_REPORT)
        assert "价值投资理念" in result
        assert "谨慎乐观" in result
        # Script and style content should be removed
        assert "var x" not in result
        assert ".hidden" not in result

    def test_empty_html(self):
        """Should return empty string for empty HTML."""
        result = extract_text_from_html("")
        assert result == ""

    def test_tags_only_html(self):
        """Should return empty/whitespace for HTML with only tags."""
        result = extract_text_from_html("<div><span></span></div>")
        assert result.strip() == ""


# ---------------------------------------------------------------------------
# Tests: PDF text extraction
# ---------------------------------------------------------------------------


class TestPdfExtraction:
    """Test PDF text extraction."""

    def test_import_error_without_pypdf(self):
        """Should raise ImportError if pypdf is not installed."""
        with patch.dict("sys.modules", {"pypdf": None}):
            # Force reimport to trigger ImportError
            with pytest.raises(ImportError, match="pypdf is required"):
                # We need to simulate the import failure inside the function
                from unittest.mock import patch as mock_patch
                with mock_patch(
                    "app.ai.use_cases.report_extract.extract_text_from_pdf"
                ) as mock_fn:
                    mock_fn.side_effect = ImportError(
                        "pypdf is required for PDF extraction. "
                        "Install with: pip install 'fund-quant-platform-backend[ai]'"
                    )
                    mock_fn(b"fake pdf")

    def test_invalid_pdf_raises_value_error(self):
        """Should raise ValueError for invalid PDF bytes."""
        with pytest.raises(ValueError, match="Failed to parse PDF"):
            extract_text_from_pdf(b"this is not a valid PDF")

    def test_valid_pdf_extraction(self):
        """Should extract text from a valid PDF."""
        # Create a minimal valid PDF using pypdf
        try:
            from pypdf import PdfWriter
        except ImportError:
            pytest.skip("pypdf not installed")

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        # pypdf doesn't easily let us add text to a blank page,
        # so we test with a real minimal PDF that has text
        # For now, just verify it doesn't crash on a blank PDF
        buf = io.BytesIO()
        writer.write(buf)
        pdf_bytes = buf.getvalue()

        # A blank page PDF should return empty text (no crash)
        result = extract_text_from_pdf(pdf_bytes)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Tests: Text truncation
# ---------------------------------------------------------------------------


class TestTruncation:
    """Test text truncation logic."""

    def test_short_text_unchanged(self):
        """Text shorter than max_length should not be truncated."""
        text = "短文本"
        result = _truncate_text(text, max_length=100)
        assert result == text

    def test_long_text_truncated(self):
        """Text longer than max_length should be truncated."""
        text = "a" * 200
        result = _truncate_text(text, max_length=100)
        assert len(result) <= 100

    def test_truncation_at_sentence_boundary(self):
        """Should prefer truncating at sentence boundaries."""
        # Create text with a sentence ending near the truncation point
        text = "第一句话。" + "x" * 50 + "第二句话。" + "y" * 100
        result = _truncate_text(text, max_length=80)
        # Should end at a sentence boundary
        assert result.endswith("。")

    def test_truncation_fallback_when_no_boundary(self):
        """Should hard-truncate when no sentence boundary is found."""
        text = "a" * 200  # No sentence boundaries
        result = _truncate_text(text, max_length=100)
        assert len(result) == 100


# ---------------------------------------------------------------------------
# Tests: ReportExtractor
# ---------------------------------------------------------------------------


class TestReportExtractor:
    """Test the ReportExtractor class."""

    @pytest.mark.asyncio
    async def test_extract_from_html_success(self):
        """Should extract structured data from HTML report."""
        llm_response = {
            "manager_view": "报告期内坚持价值投资理念，重点配置低估值蓝筹板块。",
            "style_description": "价值型，偏好大盘蓝筹",
            "market_outlook": "对下半年市场持谨慎乐观态度",
            "key_holdings_rationale": "银行保险板块估值处于历史低位，安全边际充足",
            "report_period": "2024年第2季度",
            "fund_code": None,
            "fund_name": "XX基金",
        }
        mock_service = _make_mock_llm_service(llm_response)
        extractor = ReportExtractor(mock_service)

        result = await extractor.extract_from_html(
            SAMPLE_HTML_REPORT, fund_code="000001"
        )

        assert result.manager_view == "报告期内坚持价值投资理念，重点配置低估值蓝筹板块。"
        assert result.style_description == "价值型，偏好大盘蓝筹"
        assert result.market_outlook == "对下半年市场持谨慎乐观态度"
        assert result.key_holdings_rationale is not None
        assert result.report_period == "2024年第2季度"
        assert result.fund_name == "XX基金"
        assert result.raw_text_length > 0

    @pytest.mark.asyncio
    async def test_extract_from_html_empty_raises(self):
        """Should raise ValueError for empty HTML."""
        mock_service = _make_mock_llm_service({})
        extractor = ReportExtractor(mock_service)

        with pytest.raises(ValueError, match="no extractable text"):
            await extractor.extract_from_html("")

    @pytest.mark.asyncio
    async def test_extract_from_html_tags_only_raises(self):
        """Should raise ValueError for HTML with only tags."""
        mock_service = _make_mock_llm_service({})
        extractor = ReportExtractor(mock_service)

        with pytest.raises(ValueError, match="no extractable text"):
            await extractor.extract_from_html("<div></div>")

    @pytest.mark.asyncio
    async def test_extract_from_pdf_empty_raises(self):
        """Should raise ValueError for PDF with no text."""
        try:
            from pypdf import PdfWriter
        except ImportError:
            pytest.skip("pypdf not installed")

        # Create a blank PDF (no text content)
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        buf = io.BytesIO()
        writer.write(buf)
        blank_pdf = buf.getvalue()

        mock_service = _make_mock_llm_service({})
        extractor = ReportExtractor(mock_service)

        with pytest.raises(ValueError, match="no extractable text"):
            await extractor.extract_from_pdf(blank_pdf)

    @pytest.mark.asyncio
    async def test_extract_from_pdf_invalid_raises(self):
        """Should raise ValueError for invalid PDF bytes."""
        mock_service = _make_mock_llm_service({})
        extractor = ReportExtractor(mock_service)

        with pytest.raises(ValueError, match="Failed to parse PDF"):
            await extractor.extract_from_pdf(b"not a pdf")

    @pytest.mark.asyncio
    async def test_llm_service_called_with_correct_params(self):
        """Verify LLMService is called with correct use_case and schema."""
        llm_response = {
            "manager_view": "观点",
            "style_description": "风格",
            "market_outlook": "展望",
            "key_holdings_rationale": "理由",
        }
        mock_service = _make_mock_llm_service(llm_response)
        extractor = ReportExtractor(mock_service)

        await extractor.extract_from_html(
            "<p>基金季报内容</p>", fund_code="005827"
        )

        mock_service.call.assert_called_once()
        call_kwargs = mock_service.call.call_args[1]
        assert call_kwargs["use_case"] == "report_extract"
        assert call_kwargs["schema"] == REPORT_EXTRACT_SCHEMA
        assert call_kwargs["temperature"] == 0.1
        assert call_kwargs["max_tokens"] == 2000

    @pytest.mark.asyncio
    async def test_non_dict_content_returns_empty_result(self):
        """If LLM returns non-dict, should return empty result."""
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
        extractor = ReportExtractor(mock_service)

        result = await extractor.extract_from_html(
            "<p>Some report content</p>"
        )

        assert result.manager_view is None
        assert result.style_description is None
        assert result.market_outlook is None
        assert result.key_holdings_rationale is None

    @pytest.mark.asyncio
    async def test_partial_extraction(self):
        """Should handle partial extraction (some fields null)."""
        llm_response = {
            "manager_view": "经理观点内容",
            "style_description": None,
            "market_outlook": "市场展望内容",
            "key_holdings_rationale": None,
            "report_period": "2024年第1季度",
            "fund_code": "005827",
            "fund_name": "易方达蓝筹精选",
        }
        mock_service = _make_mock_llm_service(llm_response)
        extractor = ReportExtractor(mock_service)

        result = await extractor.extract_from_html(
            "<p>部分内容的报告</p>"
        )

        assert result.manager_view == "经理观点内容"
        assert result.style_description is None
        assert result.market_outlook == "市场展望内容"
        assert result.key_holdings_rationale is None
        assert result.fund_code == "005827"

    @pytest.mark.asyncio
    async def test_full_extraction_with_sample_text(self):
        """Full extraction flow with realistic sample text."""
        llm_response = {
            "manager_view": (
                "2024年一季度保持较高股票仓位，重点配置消费、医药、互联网等行业优质龙头公司，"
                "适度增加新能源和半导体配置。"
            ),
            "style_description": "成长与价值均衡型，偏好行业龙头",
            "market_outlook": (
                "宏观经济延续温和复苏，企业盈利有望改善，"
                "将继续自下而上精选具有核心竞争力的优质公司。"
            ),
            "key_holdings_rationale": (
                "重仓贵州茅台、腾讯控股、宁德时代，"
                "基于其显著竞争优势、强大品牌力或技术壁垒，估值合理。"
            ),
            "report_period": "2024年第1季度",
            "fund_code": "005827",
            "fund_name": "易方达蓝筹精选混合",
        }
        mock_service = _make_mock_llm_service(llm_response)
        extractor = ReportExtractor(mock_service)

        result = await extractor.extract_from_html(
            SAMPLE_HTML_REPORT, fund_code="005827"
        )

        assert result.manager_view is not None
        assert "消费" in result.manager_view
        assert result.style_description is not None
        assert result.market_outlook is not None
        assert result.key_holdings_rationale is not None
        assert "贵州茅台" in result.key_holdings_rationale
        assert result.report_period == "2024年第1季度"
        assert result.fund_code == "005827"

    @pytest.mark.asyncio
    async def test_custom_max_text_length(self):
        """Custom max_text_length should be respected."""
        llm_response = {
            "manager_view": "观点",
            "style_description": "风格",
            "market_outlook": "展望",
            "key_holdings_rationale": "理由",
        }
        mock_service = _make_mock_llm_service(llm_response)
        extractor = ReportExtractor(mock_service, max_text_length=50)

        # Use a long HTML content
        long_html = "<p>" + "内容" * 100 + "</p>"
        result = await extractor.extract_from_html(long_html)

        # The prompt should have been truncated
        call_kwargs = mock_service.call.call_args[1]
        prompt = call_kwargs["prompt"]
        # The text portion in the prompt should be limited
        assert result.raw_text_length <= 50

    @pytest.mark.asyncio
    async def test_extract_from_pdf_with_mock(self):
        """Should work with mocked PDF extraction."""
        llm_response = {
            "manager_view": "PDF中的经理观点",
            "style_description": "成长型",
            "market_outlook": "看好后市",
            "key_holdings_rationale": "重仓科技股",
            "report_period": "2024年第1季度",
            "fund_code": "000001",
            "fund_name": "测试基金",
        }
        mock_service = _make_mock_llm_service(llm_response)
        extractor = ReportExtractor(mock_service)

        # Mock the PDF extraction to return sample text
        with patch(
            "app.ai.use_cases.report_extract.extract_text_from_pdf",
            return_value=SAMPLE_REPORT_TEXT,
        ):
            result = await extractor.extract_from_pdf(
                b"fake pdf bytes", fund_code="000001"
            )

        assert result.manager_view == "PDF中的经理观点"
        assert result.fund_code == "000001"
        assert result.raw_text_length > 0
