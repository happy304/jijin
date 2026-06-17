"""Announcement classification and parsing use case.

Uses LLM to classify fund announcements into predefined categories and
extract structured fields (effective_date, details). A rule engine then
cross-validates the LLM output against the original text — if key fields
(dates, amounts) are inconsistent, the result is marked as
``requires_review=True``.

Pipeline:
1. Build prompt from announcement title + content snippet
2. Call LLMService with JSON Schema constraint
3. Rule-engine cross-validation (date/amount checks)
4. Return structured result with confidence flag

Requirements: 11.9, 11.10, 11.11, 11.12
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class AnnouncementCategory(str, Enum):
    """Predefined announcement categories."""

    LIMIT_PURCHASE = "LIMIT_PURCHASE"
    SUSPEND = "SUSPEND"
    DIVIDEND = "DIVIDEND"
    MANAGER_CHANGE = "MANAGER_CHANGE"
    CONTRACT_CHANGE = "CONTRACT_CHANGE"
    OTHER = "OTHER"


#: JSON Schema for the LLM output
ANNOUNCEMENT_PARSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": [c.value for c in AnnouncementCategory],
            "description": "The announcement category",
        },
        "effective_date": {
            "type": ["string", "null"],
            "description": "The effective date in YYYY-MM-DD format, or null if not applicable",
        },
        "details": {
            "type": "object",
            "description": "Additional structured details extracted from the announcement",
            "properties": {
                "limit_amount": {
                    "type": ["number", "null"],
                    "description": "Purchase limit amount in CNY (for LIMIT_PURCHASE)",
                },
                "dividend_per_share": {
                    "type": ["number", "null"],
                    "description": "Dividend per share in CNY (for DIVIDEND)",
                },
                "new_manager": {
                    "type": ["string", "null"],
                    "description": "New fund manager name (for MANAGER_CHANGE)",
                },
                "old_manager": {
                    "type": ["string", "null"],
                    "description": "Previous fund manager name (for MANAGER_CHANGE)",
                },
                "suspend_type": {
                    "type": ["string", "null"],
                    "description": "Type of suspension: subscribe/redeem/both (for SUSPEND)",
                },
                "resume_date": {
                    "type": ["string", "null"],
                    "description": "Expected resume date in YYYY-MM-DD format (for SUSPEND)",
                },
            },
            "additionalProperties": True,
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Model confidence score between 0 and 1",
        },
    },
    "required": ["category"],
    "additionalProperties": False,
}

#: System prompt for announcement classification
SYSTEM_PROMPT = """你是一个基金公告分类专家。你的任务是将基金公告分类到以下预定义类别之一，并提取关键结构化信息。

类别说明：
- LIMIT_PURCHASE: 限制大额申购/定投，通常包含限购金额
- SUSPEND: 暂停申购/赎回/定投，通常包含暂停类型和恢复日期
- DIVIDEND: 基金分红公告，包含每份分红金额和除权日
- MANAGER_CHANGE: 基金经理变更，包含新旧经理姓名
- CONTRACT_CHANGE: 基金合同变更（费率调整、投资范围变更等）
- OTHER: 不属于以上任何类别的公告

输出要求：
1. category: 必须是上述6个类别之一
2. effective_date: 公告生效日期（YYYY-MM-DD格式），如无法确定则为null
3. details: 根据类别提取的结构化字段
4. confidence: 你对分类结果的置信度（0-1之间的数字）

注意：
- 如果公告内容模糊或可能属于多个类别，选择最相关的一个并降低confidence
- 日期必须使用YYYY-MM-DD格式
- 金额单位统一为人民币元"""

#: User prompt template
USER_PROMPT_TEMPLATE = """请分析以下基金公告并输出JSON格式的分类结果：

公告标题：{title}

公告内容摘要：
{content}

请严格按照JSON Schema输出结果。"""


# ---------------------------------------------------------------------------
# Rule engine for cross-validation
# ---------------------------------------------------------------------------

# Date patterns commonly found in Chinese fund announcements
_DATE_PATTERNS = [
    # 2024年1月15日
    re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"),
    # 2024-01-15 or 2024/01/15
    re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})"),
]

# Amount patterns (Chinese yuan)
_AMOUNT_PATTERNS = [
    # 100万元 / 100 万元
    re.compile(r"(\d+(?:\.\d+)?)\s*万\s*元"),
    # 1000元
    re.compile(r"(\d+(?:\.\d+)?)\s*元"),
]

# Category keyword indicators for rule-based validation
_CATEGORY_KEYWORDS: dict[AnnouncementCategory, list[str]] = {
    AnnouncementCategory.LIMIT_PURCHASE: [
        "限制大额申购", "暂停大额申购", "限购", "大额申购",
        "限制申购金额", "单日申购限额",
    ],
    AnnouncementCategory.SUSPEND: [
        "暂停申购", "暂停赎回", "暂停申赎", "暂停定投",
        "暂停大额", "停止申购",
    ],
    AnnouncementCategory.DIVIDEND: [
        "分红", "派息", "红利", "每份基金份额",
        "权益登记日", "除息日",
    ],
    AnnouncementCategory.MANAGER_CHANGE: [
        "基金经理变更", "增聘基金经理", "解聘基金经理",
        "基金经理离任", "新任基金经理",
    ],
    AnnouncementCategory.CONTRACT_CHANGE: [
        "基金合同", "合同变更", "费率调整", "投资范围",
        "基金份额持有人大会", "托管协议",
    ],
}


@dataclass
class RuleValidationResult:
    """Result of rule-engine cross-validation.

    Attributes:
        is_consistent: Whether the LLM output is consistent with the text.
        issues: List of inconsistency descriptions.
        keyword_match: Whether category keywords were found in the text.
    """

    is_consistent: bool = True
    issues: list[str] = field(default_factory=list)
    keyword_match: bool = False


def _extract_dates_from_text(text: str) -> list[date]:
    """Extract all dates found in the announcement text."""
    dates: list[date] = []
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            try:
                year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
                dates.append(date(year, month, day))
            except (ValueError, IndexError):
                continue
    return dates


def _extract_amounts_from_text(text: str) -> list[float]:
    """Extract monetary amounts from the text (in CNY).

    Amounts with '万元' are multiplied by 10000.
    """
    amounts: list[float] = []
    # First check for 万元 amounts
    wan_pattern = _AMOUNT_PATTERNS[0]
    for match in wan_pattern.finditer(text):
        try:
            amounts.append(float(match.group(1)) * 10000)
        except (ValueError, IndexError):
            continue
    # Then check for plain 元 amounts (exclude those already captured as 万元)
    yuan_pattern = _AMOUNT_PATTERNS[1]
    for match in yuan_pattern.finditer(text):
        # Skip if this is part of a 万元 match
        start = match.start()
        preceding = text[max(0, start - 2):start]
        if "万" in preceding:
            continue
        try:
            amounts.append(float(match.group(1)))
        except (ValueError, IndexError):
            continue
    return amounts


def _parse_date_str(date_str: str | None) -> date | None:
    """Parse a YYYY-MM-DD date string, returning None on failure."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def validate_with_rules(
    parsed: dict[str, Any],
    title: str,
    content: str,
) -> RuleValidationResult:
    """Cross-validate LLM output against the original announcement text.

    Checks:
    1. Category keywords present in text (soft check)
    2. Effective date exists in the original text
    3. Amounts in details match amounts found in text
    4. Confidence threshold

    Args:
        parsed: The LLM-parsed structured output.
        title: Original announcement title.
        content: Original announcement content/snippet.

    Returns:
        RuleValidationResult with consistency assessment.
    """
    result = RuleValidationResult()
    full_text = f"{title} {content}"
    category_str = parsed.get("category", "OTHER")

    try:
        category = AnnouncementCategory(category_str)
    except ValueError:
        result.is_consistent = False
        result.issues.append(f"Invalid category: {category_str}")
        return result

    # --- Check 1: Category keyword validation ---
    keywords = _CATEGORY_KEYWORDS.get(category, [])
    if keywords:
        found = any(kw in full_text for kw in keywords)
        result.keyword_match = found
        if not found and category != AnnouncementCategory.OTHER:
            result.issues.append(
                f"No keywords for category '{category.value}' found in text"
            )

    # --- Check 2: Effective date validation ---
    effective_date_str = parsed.get("effective_date")
    if effective_date_str:
        effective_date = _parse_date_str(effective_date_str)
        if effective_date is None:
            result.is_consistent = False
            result.issues.append(
                f"Invalid date format: '{effective_date_str}'"
            )
        else:
            text_dates = _extract_dates_from_text(full_text)
            if text_dates and effective_date not in text_dates:
                result.issues.append(
                    f"Effective date {effective_date_str} not found in text "
                    f"(found: {[str(d) for d in text_dates]})"
                )

    # --- Check 3: Amount validation ---
    details = parsed.get("details") or {}
    limit_amount = details.get("limit_amount")
    if limit_amount is not None and category == AnnouncementCategory.LIMIT_PURCHASE:
        text_amounts = _extract_amounts_from_text(full_text)
        if text_amounts and limit_amount not in text_amounts:
            # Allow some tolerance (within 1% or exact match after rounding)
            close_match = any(
                abs(limit_amount - amt) / max(amt, 1) < 0.01
                for amt in text_amounts
            )
            if not close_match:
                result.issues.append(
                    f"Limit amount {limit_amount} not found in text amounts "
                    f"(found: {text_amounts})"
                )

    dividend_per_share = details.get("dividend_per_share")
    if dividend_per_share is not None and category == AnnouncementCategory.DIVIDEND:
        # Check if the dividend amount appears in the text
        text_str = full_text
        dividend_str = f"{dividend_per_share}"
        if dividend_str not in text_str:
            # Try common formats
            formatted = f"{dividend_per_share:.4f}"
            if formatted not in text_str and dividend_str not in text_str:
                result.issues.append(
                    f"Dividend per share {dividend_per_share} not found in text"
                )

    # --- Check 4: Confidence threshold ---
    confidence = parsed.get("confidence", 0.5)
    if confidence < 0.6:
        result.issues.append(f"Low confidence: {confidence}")

    # Determine overall consistency
    if result.issues:
        # Hard failures make it inconsistent
        hard_failures = [
            i for i in result.issues
            if "Invalid" in i or "not found in text" in i
        ]
        if hard_failures:
            result.is_consistent = False

    return result


# ---------------------------------------------------------------------------
# Main use case
# ---------------------------------------------------------------------------


@dataclass
class AnnouncementParseResult:
    """Result of announcement parsing.

    Attributes:
        category: Classified category.
        effective_date: Extracted effective date (if any).
        details: Additional structured details.
        requires_review: Whether human review is needed.
        confidence: Model confidence score.
        validation_issues: Issues found during rule validation.
    """

    category: AnnouncementCategory
    effective_date: date | None = None
    details: dict[str, Any] = field(default_factory=dict)
    requires_review: bool = False
    confidence: float = 0.5
    validation_issues: list[str] = field(default_factory=list)


class AnnouncementParser:
    """Parses and classifies fund announcements using LLM + rule engine.

    This class orchestrates the full parsing pipeline:
    1. Build prompt from announcement text
    2. Call LLMService for classification
    3. Cross-validate with rule engine
    4. Mark uncertain results for review

    Args:
        llm_service: The unified LLMService instance.
        confidence_threshold: Below this threshold, mark as requires_review.
            Defaults to 0.7.
    """

    USE_CASE = "announcement_parse"

    def __init__(
        self,
        llm_service: Any,
        *,
        confidence_threshold: float = 0.7,
    ) -> None:
        self._llm = llm_service
        self._confidence_threshold = confidence_threshold

    async def parse(
        self,
        title: str,
        content: str = "",
        *,
        fund_code: str | None = None,
    ) -> AnnouncementParseResult:
        """Parse a single announcement.

        Args:
            title: Announcement title.
            content: Announcement content or snippet. May be empty if
                only the title is available.
            fund_code: Optional fund code for logging context.

        Returns:
            AnnouncementParseResult with classification and extracted data.

        Raises:
            AllProvidersFailedError: If all LLM providers fail (propagated
                from LLMService).
        """
        # Build the user prompt
        prompt = USER_PROMPT_TEMPLATE.format(
            title=title,
            content=content or "(无正文内容，仅根据标题分类)",
        )

        # Call LLM service
        from app.ai.service import LLMResult

        llm_result: LLMResult = await self._llm.call(
            use_case=self.USE_CASE,
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            schema=ANNOUNCEMENT_PARSE_SCHEMA,
            temperature=0.1,
            max_tokens=500,
        )

        # Extract parsed content
        parsed: dict[str, Any]
        if isinstance(llm_result.content, dict):
            parsed = llm_result.content
        else:
            # Should not happen with schema validation, but handle gracefully
            log.warning(
                "announcement_parse.unexpected_content_type",
                content_type=type(llm_result.content).__name__,
                fund_code=fund_code,
            )
            return AnnouncementParseResult(
                category=AnnouncementCategory.OTHER,
                requires_review=True,
                confidence=0.0,
                validation_issues=["LLM returned non-dict content"],
            )

        # Parse the result
        category_str = parsed.get("category", "OTHER")
        try:
            category = AnnouncementCategory(category_str)
        except ValueError:
            category = AnnouncementCategory.OTHER

        effective_date = _parse_date_str(parsed.get("effective_date"))
        details = parsed.get("details") or {}
        confidence = parsed.get("confidence", 0.5)

        # Rule-engine cross-validation
        validation = validate_with_rules(parsed, title, content)

        # Determine if review is needed
        requires_review = (
            not validation.is_consistent
            or confidence < self._confidence_threshold
            or (not validation.keyword_match and category != AnnouncementCategory.OTHER)
        )

        if requires_review:
            log.info(
                "announcement_parse.requires_review",
                fund_code=fund_code,
                category=category.value,
                confidence=confidence,
                issues=validation.issues,
            )

        return AnnouncementParseResult(
            category=category,
            effective_date=effective_date,
            details=details,
            requires_review=requires_review,
            confidence=confidence,
            validation_issues=validation.issues,
        )


# ---------------------------------------------------------------------------
# Celery task for async parsing trigger
# ---------------------------------------------------------------------------


def trigger_announcement_parse(announcement_id: int) -> None:
    """Trigger async announcement parsing after ingestion.

    This function sends a Celery task to the 'ai' queue to parse
    a newly ingested announcement. Called from the ingest pipeline
    after announcements are committed to the database.

    Args:
        announcement_id: The database ID of the announcement to parse.
    """
    from app.tasks.celery_app import celery_app

    celery_app.send_task(
        "app.tasks.ingest.parse_announcement",
        args=[announcement_id],
        queue="ai",
    )


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "ANNOUNCEMENT_PARSE_SCHEMA",
    "AnnouncementCategory",
    "AnnouncementParseResult",
    "AnnouncementParser",
    "RuleValidationResult",
    "SYSTEM_PROMPT",
    "USER_PROMPT_TEMPLATE",
    "trigger_announcement_parse",
    "validate_with_rules",
]
