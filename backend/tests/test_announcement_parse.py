"""Tests for announcement classification and parsing use case (task 7.6).

Covers:
- JSON Schema definition correctness
- Rule-engine cross-validation logic
- AnnouncementParser integration with mock LLMService
- Edge cases: empty content, low confidence, invalid dates
- Historical announcement samples for realistic testing

Requirements: 11.9, 11.10, 11.11, 11.12
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.provider import LLMResponse, Message
from app.ai.service import LLMResult
from app.ai.use_cases.announcement_parse import (
    ANNOUNCEMENT_PARSE_SCHEMA,
    AnnouncementCategory,
    AnnouncementParseResult,
    AnnouncementParser,
    RuleValidationResult,
    validate_with_rules,
    _extract_amounts_from_text,
    _extract_dates_from_text,
    _parse_date_str,
    trigger_announcement_parse,
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
        cost_usd=0.001,
        latency_ms=100,
    )


def _make_mock_llm_service(response_content: dict[str, Any]) -> AsyncMock:
    """Create a mock LLMService that returns the given content."""
    service = AsyncMock()
    service.call = AsyncMock(return_value=_make_llm_result(response_content))
    return service


# ---------------------------------------------------------------------------
# Sample announcements (historical-style)
# ---------------------------------------------------------------------------

SAMPLE_LIMIT_PURCHASE = {
    "title": "关于XX基金暂停大额申购、大额转换转入及大额定期定额投资业务的公告",
    "content": (
        "自2024年3月15日起，本基金暂停接受单日单个基金账户单笔或累计超过100万元"
        "（不含100万元）的申购申请。投资者通过定期定额投资方式申购本基金时，"
        "单笔申购金额不得超过100万元。"
    ),
}

SAMPLE_SUSPEND = {
    "title": "关于XX基金暂停申购业务的公告",
    "content": (
        "自2024年2月1日起暂停本基金的申购业务，暂停期间不接受申购申请。"
        "赎回业务照常办理。恢复申购业务的具体时间将另行公告。"
    ),
}

SAMPLE_DIVIDEND = {
    "title": "关于XX基金2024年第1次分红的公告",
    "content": (
        "权益登记日为2024年1月20日，除息日为2024年1月20日，"
        "每份基金份额派发红利0.0500元。红利发放日为2024年1月22日。"
    ),
}

SAMPLE_MANAGER_CHANGE = {
    "title": "关于XX基金基金经理变更的公告",
    "content": (
        "经公司决定，自2024年4月1日起，增聘李明先生担任本基金基金经理，"
        "与原基金经理张华女士共同管理本基金。"
    ),
}

SAMPLE_CONTRACT_CHANGE = {
    "title": "关于XX基金修改基金合同的公告",
    "content": (
        "经基金份额持有人大会决议通过，自2024年5月10日起，"
        "本基金投资范围增加港股通标的股票，管理费率由1.50%调整为1.20%。"
    ),
}

SAMPLE_OTHER = {
    "title": "关于XX基金2023年年度报告的提示性公告",
    "content": "本基金2023年年度报告已于2024年3月30日在指定媒体上披露。",
}


# ---------------------------------------------------------------------------
# Tests: Schema definition
# ---------------------------------------------------------------------------


class TestSchema:
    """Test the JSON Schema definition."""

    def test_schema_has_required_fields(self):
        """Schema must require 'category' field."""
        assert "required" in ANNOUNCEMENT_PARSE_SCHEMA
        assert "category" in ANNOUNCEMENT_PARSE_SCHEMA["required"]

    def test_schema_category_enum(self):
        """Category enum must contain all expected values."""
        category_enum = ANNOUNCEMENT_PARSE_SCHEMA["properties"]["category"]["enum"]
        expected = [
            "LIMIT_PURCHASE", "SUSPEND", "DIVIDEND",
            "MANAGER_CHANGE", "CONTRACT_CHANGE", "OTHER",
        ]
        assert set(category_enum) == set(expected)

    def test_schema_effective_date_nullable(self):
        """effective_date should be nullable string."""
        date_prop = ANNOUNCEMENT_PARSE_SCHEMA["properties"]["effective_date"]
        assert "null" in date_prop["type"] or date_prop["type"] == ["string", "null"]

    def test_schema_details_is_object(self):
        """details should be an object type."""
        details_prop = ANNOUNCEMENT_PARSE_SCHEMA["properties"]["details"]
        assert details_prop["type"] == "object"

    def test_schema_confidence_bounds(self):
        """confidence should be bounded between 0 and 1."""
        conf_prop = ANNOUNCEMENT_PARSE_SCHEMA["properties"]["confidence"]
        assert conf_prop["minimum"] == 0
        assert conf_prop["maximum"] == 1


# ---------------------------------------------------------------------------
# Tests: Date extraction
# ---------------------------------------------------------------------------


class TestDateExtraction:
    """Test date extraction from Chinese text."""

    def test_chinese_date_format(self):
        """Should extract dates in '2024年3月15日' format."""
        text = "自2024年3月15日起暂停申购"
        dates = _extract_dates_from_text(text)
        assert date(2024, 3, 15) in dates

    def test_iso_date_format(self):
        """Should extract dates in '2024-03-15' format."""
        text = "生效日期为2024-03-15"
        dates = _extract_dates_from_text(text)
        assert date(2024, 3, 15) in dates

    def test_slash_date_format(self):
        """Should extract dates in '2024/03/15' format."""
        text = "公告日期2024/3/15"
        dates = _extract_dates_from_text(text)
        assert date(2024, 3, 15) in dates

    def test_multiple_dates(self):
        """Should extract multiple dates from text."""
        text = "权益登记日为2024年1月20日，红利发放日为2024年1月22日"
        dates = _extract_dates_from_text(text)
        assert date(2024, 1, 20) in dates
        assert date(2024, 1, 22) in dates

    def test_no_dates(self):
        """Should return empty list when no dates found."""
        text = "本基金年度报告已披露"
        dates = _extract_dates_from_text(text)
        assert dates == []

    def test_invalid_date_skipped(self):
        """Invalid dates (e.g. month 13) should be skipped."""
        text = "2024年13月40日"
        dates = _extract_dates_from_text(text)
        assert dates == []


# ---------------------------------------------------------------------------
# Tests: Amount extraction
# ---------------------------------------------------------------------------


class TestAmountExtraction:
    """Test monetary amount extraction."""

    def test_wan_yuan(self):
        """Should extract amounts in '万元' format."""
        text = "单笔申购金额不得超过100万元"
        amounts = _extract_amounts_from_text(text)
        assert 1_000_000.0 in amounts

    def test_yuan(self):
        """Should extract amounts in '元' format."""
        text = "每份基金份额派发红利0.0500元"
        amounts = _extract_amounts_from_text(text)
        assert 0.05 in amounts

    def test_decimal_wan_yuan(self):
        """Should handle decimal amounts with 万元."""
        text = "限额为1.5万元"
        amounts = _extract_amounts_from_text(text)
        assert 15000.0 in amounts

    def test_no_amounts(self):
        """Should return empty list when no amounts found."""
        text = "基金经理变更公告"
        amounts = _extract_amounts_from_text(text)
        assert amounts == []


# ---------------------------------------------------------------------------
# Tests: Date string parsing
# ---------------------------------------------------------------------------


class TestParseDateStr:
    """Test _parse_date_str helper."""

    def test_valid_date(self):
        assert _parse_date_str("2024-03-15") == date(2024, 3, 15)

    def test_none_input(self):
        assert _parse_date_str(None) is None

    def test_empty_string(self):
        assert _parse_date_str("") is None

    def test_invalid_format(self):
        assert _parse_date_str("15/03/2024") is None

    def test_invalid_date(self):
        assert _parse_date_str("2024-13-40") is None


# ---------------------------------------------------------------------------
# Tests: Rule engine cross-validation
# ---------------------------------------------------------------------------


class TestRuleValidation:
    """Test the rule-engine cross-validation logic."""

    def test_consistent_limit_purchase(self):
        """Consistent LIMIT_PURCHASE should pass validation."""
        parsed = {
            "category": "LIMIT_PURCHASE",
            "effective_date": "2024-03-15",
            "details": {"limit_amount": 1_000_000},
            "confidence": 0.9,
        }
        result = validate_with_rules(
            parsed,
            SAMPLE_LIMIT_PURCHASE["title"],
            SAMPLE_LIMIT_PURCHASE["content"],
        )
        assert result.is_consistent is True
        assert result.keyword_match is True

    def test_consistent_dividend(self):
        """Consistent DIVIDEND should pass validation."""
        parsed = {
            "category": "DIVIDEND",
            "effective_date": "2024-01-20",
            "details": {"dividend_per_share": 0.05},
            "confidence": 0.95,
        }
        result = validate_with_rules(
            parsed,
            SAMPLE_DIVIDEND["title"],
            SAMPLE_DIVIDEND["content"],
        )
        assert result.is_consistent is True
        assert result.keyword_match is True

    def test_date_not_in_text(self):
        """Date not found in text should add an issue."""
        parsed = {
            "category": "LIMIT_PURCHASE",
            "effective_date": "2025-12-31",  # Not in text
            "details": {},
            "confidence": 0.8,
        }
        result = validate_with_rules(
            parsed,
            SAMPLE_LIMIT_PURCHASE["title"],
            SAMPLE_LIMIT_PURCHASE["content"],
        )
        assert any("not found in text" in issue for issue in result.issues)

    def test_invalid_date_format(self):
        """Invalid date format should mark as inconsistent."""
        parsed = {
            "category": "SUSPEND",
            "effective_date": "not-a-date",
            "details": {},
            "confidence": 0.8,
        }
        result = validate_with_rules(
            parsed,
            SAMPLE_SUSPEND["title"],
            SAMPLE_SUSPEND["content"],
        )
        assert result.is_consistent is False
        assert any("Invalid date format" in issue for issue in result.issues)

    def test_no_keywords_for_category(self):
        """Missing keywords for non-OTHER category should add issue."""
        parsed = {
            "category": "MANAGER_CHANGE",
            "effective_date": None,
            "details": {},
            "confidence": 0.8,
        }
        # Use text that has no manager-change keywords
        result = validate_with_rules(
            parsed,
            "关于基金年度报告的公告",
            "本基金年度报告已披露",
        )
        assert not result.keyword_match
        assert any("No keywords" in issue for issue in result.issues)

    def test_other_category_no_keyword_check(self):
        """OTHER category should not require keyword matches."""
        parsed = {
            "category": "OTHER",
            "effective_date": None,
            "details": {},
            "confidence": 0.8,
        }
        result = validate_with_rules(
            parsed,
            SAMPLE_OTHER["title"],
            SAMPLE_OTHER["content"],
        )
        # OTHER doesn't need keyword match
        assert not any("No keywords" in issue for issue in result.issues)

    def test_low_confidence_adds_issue(self):
        """Low confidence should add an issue."""
        parsed = {
            "category": "SUSPEND",
            "effective_date": "2024-02-01",
            "details": {},
            "confidence": 0.3,
        }
        result = validate_with_rules(
            parsed,
            SAMPLE_SUSPEND["title"],
            SAMPLE_SUSPEND["content"],
        )
        assert any("Low confidence" in issue for issue in result.issues)

    def test_invalid_category_value(self):
        """Invalid category value should mark as inconsistent."""
        parsed = {
            "category": "INVALID_CATEGORY",
            "effective_date": None,
            "details": {},
            "confidence": 0.8,
        }
        result = validate_with_rules(parsed, "title", "content")
        assert result.is_consistent is False
        assert any("Invalid category" in issue for issue in result.issues)

    def test_amount_mismatch(self):
        """Amount not matching text should add issue."""
        parsed = {
            "category": "LIMIT_PURCHASE",
            "effective_date": "2024-03-15",
            "details": {"limit_amount": 500_000},  # Text says 100万=1000000
            "confidence": 0.8,
        }
        result = validate_with_rules(
            parsed,
            SAMPLE_LIMIT_PURCHASE["title"],
            SAMPLE_LIMIT_PURCHASE["content"],
        )
        assert any("Limit amount" in issue for issue in result.issues)


# ---------------------------------------------------------------------------
# Tests: AnnouncementParser
# ---------------------------------------------------------------------------


class TestAnnouncementParser:
    """Test the AnnouncementParser class."""

    @pytest.mark.asyncio
    async def test_parse_limit_purchase(self):
        """Should correctly parse a limit purchase announcement."""
        llm_response = {
            "category": "LIMIT_PURCHASE",
            "effective_date": "2024-03-15",
            "details": {"limit_amount": 1_000_000},
            "confidence": 0.92,
        }
        mock_service = _make_mock_llm_service(llm_response)
        parser = AnnouncementParser(mock_service)

        result = await parser.parse(
            title=SAMPLE_LIMIT_PURCHASE["title"],
            content=SAMPLE_LIMIT_PURCHASE["content"],
            fund_code="000001",
        )

        assert result.category == AnnouncementCategory.LIMIT_PURCHASE
        assert result.effective_date == date(2024, 3, 15)
        assert result.confidence == 0.92
        assert result.requires_review is False

    @pytest.mark.asyncio
    async def test_parse_dividend(self):
        """Should correctly parse a dividend announcement."""
        llm_response = {
            "category": "DIVIDEND",
            "effective_date": "2024-01-20",
            "details": {"dividend_per_share": 0.05},
            "confidence": 0.95,
        }
        mock_service = _make_mock_llm_service(llm_response)
        parser = AnnouncementParser(mock_service)

        result = await parser.parse(
            title=SAMPLE_DIVIDEND["title"],
            content=SAMPLE_DIVIDEND["content"],
        )

        assert result.category == AnnouncementCategory.DIVIDEND
        assert result.effective_date == date(2024, 1, 20)
        assert result.requires_review is False

    @pytest.mark.asyncio
    async def test_parse_suspend(self):
        """Should correctly parse a suspend announcement."""
        llm_response = {
            "category": "SUSPEND",
            "effective_date": "2024-02-01",
            "details": {"suspend_type": "subscribe"},
            "confidence": 0.88,
        }
        mock_service = _make_mock_llm_service(llm_response)
        parser = AnnouncementParser(mock_service)

        result = await parser.parse(
            title=SAMPLE_SUSPEND["title"],
            content=SAMPLE_SUSPEND["content"],
        )

        assert result.category == AnnouncementCategory.SUSPEND
        assert result.effective_date == date(2024, 2, 1)
        assert result.requires_review is False

    @pytest.mark.asyncio
    async def test_parse_manager_change(self):
        """Should correctly parse a manager change announcement."""
        llm_response = {
            "category": "MANAGER_CHANGE",
            "effective_date": "2024-04-01",
            "details": {
                "new_manager": "李明",
                "old_manager": "张华",
            },
            "confidence": 0.90,
        }
        mock_service = _make_mock_llm_service(llm_response)
        parser = AnnouncementParser(mock_service)

        result = await parser.parse(
            title=SAMPLE_MANAGER_CHANGE["title"],
            content=SAMPLE_MANAGER_CHANGE["content"],
        )

        assert result.category == AnnouncementCategory.MANAGER_CHANGE
        assert result.effective_date == date(2024, 4, 1)
        assert result.details["new_manager"] == "李明"

    @pytest.mark.asyncio
    async def test_low_confidence_marks_review(self):
        """Low confidence should mark requires_review=True."""
        llm_response = {
            "category": "CONTRACT_CHANGE",
            "effective_date": "2024-05-10",
            "details": {},
            "confidence": 0.4,  # Below threshold
        }
        mock_service = _make_mock_llm_service(llm_response)
        parser = AnnouncementParser(mock_service, confidence_threshold=0.7)

        result = await parser.parse(
            title=SAMPLE_CONTRACT_CHANGE["title"],
            content=SAMPLE_CONTRACT_CHANGE["content"],
        )

        assert result.requires_review is True

    @pytest.mark.asyncio
    async def test_inconsistent_date_marks_review(self):
        """Inconsistent date should mark requires_review=True."""
        llm_response = {
            "category": "LIMIT_PURCHASE",
            "effective_date": "2099-12-31",  # Not in text
            "details": {},
            "confidence": 0.85,
        }
        mock_service = _make_mock_llm_service(llm_response)
        parser = AnnouncementParser(mock_service)

        result = await parser.parse(
            title=SAMPLE_LIMIT_PURCHASE["title"],
            content=SAMPLE_LIMIT_PURCHASE["content"],
        )

        # Date not found in text → issues → requires_review
        assert result.requires_review is True

    @pytest.mark.asyncio
    async def test_empty_content_uses_title_only(self):
        """Should work with empty content (title-only classification)."""
        llm_response = {
            "category": "DIVIDEND",
            "effective_date": None,
            "details": {},
            "confidence": 0.75,
        }
        mock_service = _make_mock_llm_service(llm_response)
        parser = AnnouncementParser(mock_service)

        result = await parser.parse(
            title="关于XX基金分红的公告",
            content="",
        )

        assert result.category == AnnouncementCategory.DIVIDEND

    @pytest.mark.asyncio
    async def test_non_dict_content_returns_other_with_review(self):
        """If LLM returns non-dict, should return OTHER with review flag."""
        mock_service = AsyncMock()
        mock_service.call = AsyncMock(
            return_value=LLMResult(
                content="unexpected string",
                raw_response=None,
                provider="test",
                model="test",
                cached=False,
                cost_usd=0.0,
                latency_ms=0,
            )
        )
        parser = AnnouncementParser(mock_service)

        result = await parser.parse(title="Some title", content="Some content")

        assert result.category == AnnouncementCategory.OTHER
        assert result.requires_review is True

    @pytest.mark.asyncio
    async def test_invalid_category_defaults_to_other(self):
        """Invalid category from LLM should default to OTHER."""
        llm_response = {
            "category": "UNKNOWN_TYPE",
            "effective_date": None,
            "details": {},
            "confidence": 0.5,
        }
        mock_service = _make_mock_llm_service(llm_response)
        parser = AnnouncementParser(mock_service)

        result = await parser.parse(title="Some title", content="Some content")

        assert result.category == AnnouncementCategory.OTHER

    @pytest.mark.asyncio
    async def test_llm_service_called_with_correct_params(self):
        """Verify LLMService is called with correct use_case and schema."""
        llm_response = {
            "category": "OTHER",
            "effective_date": None,
            "details": {},
            "confidence": 0.8,
        }
        mock_service = _make_mock_llm_service(llm_response)
        parser = AnnouncementParser(mock_service)

        await parser.parse(title="Test", content="Test content")

        mock_service.call.assert_called_once()
        call_kwargs = mock_service.call.call_args
        assert call_kwargs[1]["use_case"] == "announcement_parse"
        assert call_kwargs[1]["schema"] == ANNOUNCEMENT_PARSE_SCHEMA
        assert call_kwargs[1]["temperature"] == 0.1

    @pytest.mark.asyncio
    async def test_custom_confidence_threshold(self):
        """Custom confidence threshold should be respected."""
        llm_response = {
            "category": "SUSPEND",
            "effective_date": "2024-02-01",
            "details": {},
            "confidence": 0.85,
        }
        mock_service = _make_mock_llm_service(llm_response)

        # With high threshold, 0.85 should trigger review
        parser = AnnouncementParser(mock_service, confidence_threshold=0.9)
        result = await parser.parse(
            title=SAMPLE_SUSPEND["title"],
            content=SAMPLE_SUSPEND["content"],
        )
        assert result.requires_review is True

        # With lower threshold, 0.85 should pass
        mock_service.call.reset_mock()
        mock_service.call.return_value = _make_llm_result(llm_response)
        parser2 = AnnouncementParser(mock_service, confidence_threshold=0.7)
        result2 = await parser2.parse(
            title=SAMPLE_SUSPEND["title"],
            content=SAMPLE_SUSPEND["content"],
        )
        assert result2.requires_review is False


# ---------------------------------------------------------------------------
# Tests: trigger_announcement_parse
# ---------------------------------------------------------------------------


class TestTriggerParse:
    """Test the async trigger function."""

    def test_trigger_sends_celery_task(self):
        """trigger_announcement_parse should send a Celery task."""
        with patch("app.tasks.celery_app.celery_app") as mock_celery:
            mock_celery.send_task = MagicMock()
            trigger_announcement_parse(42)

            mock_celery.send_task.assert_called_once_with(
                "app.tasks.ingest.parse_announcement",
                args=[42],
                queue="ai",
            )


# ---------------------------------------------------------------------------
# Tests: Historical announcement samples (integration-style)
# ---------------------------------------------------------------------------


class TestHistoricalSamples:
    """Test with realistic historical announcement samples."""

    @pytest.mark.asyncio
    async def test_sample_limit_purchase_full_flow(self):
        """Full flow with a realistic limit purchase announcement."""
        llm_response = {
            "category": "LIMIT_PURCHASE",
            "effective_date": "2024-03-15",
            "details": {"limit_amount": 1_000_000},
            "confidence": 0.93,
        }
        mock_service = _make_mock_llm_service(llm_response)
        parser = AnnouncementParser(mock_service)

        result = await parser.parse(
            title=SAMPLE_LIMIT_PURCHASE["title"],
            content=SAMPLE_LIMIT_PURCHASE["content"],
            fund_code="110011",
        )

        assert result.category == AnnouncementCategory.LIMIT_PURCHASE
        assert result.effective_date == date(2024, 3, 15)
        assert result.details.get("limit_amount") == 1_000_000
        assert result.requires_review is False
        assert result.confidence > 0.9

    @pytest.mark.asyncio
    async def test_sample_suspend_full_flow(self):
        """Full flow with a realistic suspend announcement."""
        llm_response = {
            "category": "SUSPEND",
            "effective_date": "2024-02-01",
            "details": {"suspend_type": "subscribe"},
            "confidence": 0.91,
        }
        mock_service = _make_mock_llm_service(llm_response)
        parser = AnnouncementParser(mock_service)

        result = await parser.parse(
            title=SAMPLE_SUSPEND["title"],
            content=SAMPLE_SUSPEND["content"],
            fund_code="000001",
        )

        assert result.category == AnnouncementCategory.SUSPEND
        assert result.effective_date == date(2024, 2, 1)
        assert result.requires_review is False

    @pytest.mark.asyncio
    async def test_sample_contract_change_full_flow(self):
        """Full flow with a realistic contract change announcement."""
        llm_response = {
            "category": "CONTRACT_CHANGE",
            "effective_date": "2024-05-10",
            "details": {},
            "confidence": 0.87,
        }
        mock_service = _make_mock_llm_service(llm_response)
        parser = AnnouncementParser(mock_service)

        result = await parser.parse(
            title=SAMPLE_CONTRACT_CHANGE["title"],
            content=SAMPLE_CONTRACT_CHANGE["content"],
            fund_code="519001",
        )

        assert result.category == AnnouncementCategory.CONTRACT_CHANGE
        assert result.effective_date == date(2024, 5, 10)
        assert result.requires_review is False

    @pytest.mark.asyncio
    async def test_ambiguous_announcement_marks_review(self):
        """Ambiguous announcement with wrong keywords should mark review."""
        # LLM says DIVIDEND but text has no dividend keywords
        llm_response = {
            "category": "DIVIDEND",
            "effective_date": "2024-03-30",
            "details": {},
            "confidence": 0.6,
        }
        mock_service = _make_mock_llm_service(llm_response)
        parser = AnnouncementParser(mock_service)

        result = await parser.parse(
            title=SAMPLE_OTHER["title"],  # Annual report, not dividend
            content=SAMPLE_OTHER["content"],
        )

        # Low confidence + no keyword match → requires_review
        assert result.requires_review is True
