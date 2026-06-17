"""Quarterly report holdings commentary extraction use case.

Uses LLM to extract structured information from fund quarterly/annual
report PDFs or HTML content, including:
- Manager's market view (经理观点)
- Investment style description (风格描述)
- Market outlook (市场展望)
- Key holdings rationale (重仓理由)

Pipeline:
1. Accept PDF bytes or HTML text as input
2. Extract raw text from PDF (using pypdf) or clean HTML
3. Build prompt with extracted text
4. Call LLMService with JSON Schema constraint
5. Validate and return structured result

Requirements: 11.9
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants & Schema
# ---------------------------------------------------------------------------

#: JSON Schema for the LLM structured output
REPORT_EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "manager_view": {
            "type": ["string", "null"],
            "description": "基金经理对报告期内市场和操作的总结观点",
        },
        "style_description": {
            "type": ["string", "null"],
            "description": "基金的投资风格描述（如价值型、成长型、均衡型等）",
        },
        "market_outlook": {
            "type": ["string", "null"],
            "description": "基金经理对未来市场的展望和判断",
        },
        "key_holdings_rationale": {
            "type": ["string", "null"],
            "description": "重仓股票或债券的持有理由和逻辑",
        },
        "report_period": {
            "type": ["string", "null"],
            "description": "报告期，如 '2024年第1季度'",
        },
        "fund_code": {
            "type": ["string", "null"],
            "description": "基金代码（如能从报告中提取）",
        },
        "fund_name": {
            "type": ["string", "null"],
            "description": "基金名称（如能从报告中提取）",
        },
    },
    "required": ["manager_view", "style_description", "market_outlook", "key_holdings_rationale"],
    "additionalProperties": False,
}

#: System prompt for report extraction
SYSTEM_PROMPT = """你是一个专业的基金季报/年报分析专家。你的任务是从基金报告文本中提取以下结构化信息：

1. manager_view（经理观点）：基金经理在报告期内对市场环境、操作策略的总结性观点。通常出现在"管理人报告"或"投资策略和运作分析"章节。
2. style_description（风格描述）：基金的投资风格特征，如价值型、成长型、均衡型、大盘/中小盘偏好等。从经理描述和持仓特征中推断。
3. market_outlook（市场展望）：基金经理对未来市场走势的判断和展望。通常出现在"展望"或"后市展望"章节。
4. key_holdings_rationale（重仓理由）：基金经理对重仓行业或个股的持有逻辑和理由。

提取规则：
- 只提取报告中明确表述的内容，不要推测或编造
- 如果某个字段在报告中找不到对应内容，返回 null
- 保持原文语义，可以适当精简但不要改变含义
- 每个字段的内容控制在 500 字以内
- 如果能识别出报告期和基金信息，也一并提取"""

#: User prompt template
USER_PROMPT_TEMPLATE = """请从以下基金报告文本中提取结构化信息，输出JSON格式：

---报告文本开始---
{text}
---报告文本结束---

请严格按照JSON Schema输出结果。如果某个字段在文本中找不到对应内容，该字段返回null。"""

#: Maximum text length to send to LLM (avoid token overflow)
MAX_TEXT_LENGTH = 12000


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract text content from PDF bytes using pypdf.

    Args:
        pdf_bytes: Raw PDF file content as bytes.

    Returns:
        Extracted text from all pages, concatenated.

    Raises:
        ImportError: If pypdf is not installed.
        ValueError: If the PDF cannot be parsed.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ImportError(
            "pypdf is required for PDF extraction. "
            "Install with: pip install 'fund-quant-platform-backend[ai]'"
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:
        raise ValueError(f"Failed to parse PDF: {exc}") from exc

    pages_text: list[str] = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages_text.append(text)

    return "\n".join(pages_text)


# ---------------------------------------------------------------------------
# HTML text extraction
# ---------------------------------------------------------------------------

# Regex to strip HTML tags
_HTML_TAG_RE = re.compile(r"<[^>]+>")
# Regex to collapse whitespace
_WHITESPACE_RE = re.compile(r"\s+")


def extract_text_from_html(html: str) -> str:
    """Extract plain text from HTML content.

    Uses a simple regex-based approach to strip tags and normalize
    whitespace. For fund report HTML, this is sufficient since the
    content is primarily text-based.

    Args:
        html: Raw HTML string.

    Returns:
        Cleaned plain text.
    """
    # Remove script and style blocks
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Strip remaining tags
    text = _HTML_TAG_RE.sub(" ", text)
    # Decode common HTML entities
    text = text.replace("&nbsp;", " ")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&amp;", "&")
    text = text.replace("&quot;", '"')
    # Normalize whitespace
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Text truncation
# ---------------------------------------------------------------------------


def _truncate_text(text: str, max_length: int = MAX_TEXT_LENGTH) -> str:
    """Truncate text to max_length, preserving sentence boundaries where possible."""
    if len(text) <= max_length:
        return text

    # Try to cut at a sentence boundary
    truncated = text[:max_length]
    # Look for the last sentence-ending punctuation
    last_period = max(
        truncated.rfind("。"),
        truncated.rfind("；"),
        truncated.rfind("\n"),
    )
    if last_period > max_length * 0.7:
        return truncated[: last_period + 1]
    return truncated


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ReportExtractResult:
    """Result of quarterly report extraction.

    Attributes:
        manager_view: Fund manager's market view and operational summary.
        style_description: Investment style description.
        market_outlook: Market outlook and future expectations.
        key_holdings_rationale: Rationale for key holdings.
        report_period: Report period (e.g. '2024年第1季度').
        fund_code: Fund code if extracted.
        fund_name: Fund name if extracted.
        raw_text_length: Length of the raw text sent to LLM.
    """

    manager_view: str | None = None
    style_description: str | None = None
    market_outlook: str | None = None
    key_holdings_rationale: str | None = None
    report_period: str | None = None
    fund_code: str | None = None
    fund_name: str | None = None
    raw_text_length: int = 0


# ---------------------------------------------------------------------------
# Main use case class
# ---------------------------------------------------------------------------


class ReportExtractor:
    """Extracts structured information from fund quarterly/annual reports.

    Supports both PDF and HTML input formats. Uses LLM to parse the
    report text and extract manager views, style descriptions, market
    outlook, and holdings rationale.

    Args:
        llm_service: The unified LLMService instance.
        max_text_length: Maximum text length to send to LLM.
            Defaults to 12000 characters.
    """

    USE_CASE = "report_extract"

    def __init__(
        self,
        llm_service: Any,
        *,
        max_text_length: int = MAX_TEXT_LENGTH,
    ) -> None:
        self._llm = llm_service
        self._max_text_length = max_text_length

    async def extract_from_pdf(
        self,
        pdf_bytes: bytes,
        *,
        fund_code: str | None = None,
    ) -> ReportExtractResult:
        """Extract structured data from a PDF report.

        Args:
            pdf_bytes: Raw PDF file content.
            fund_code: Optional fund code for logging context.

        Returns:
            ReportExtractResult with extracted fields.

        Raises:
            ImportError: If pypdf is not installed.
            ValueError: If the PDF cannot be parsed or is empty.
        """
        text = extract_text_from_pdf(pdf_bytes)
        if not text.strip():
            raise ValueError("PDF contains no extractable text")

        return await self._extract(text, fund_code=fund_code)

    async def extract_from_html(
        self,
        html: str,
        *,
        fund_code: str | None = None,
    ) -> ReportExtractResult:
        """Extract structured data from an HTML report.

        Args:
            html: Raw HTML content of the report.
            fund_code: Optional fund code for logging context.

        Returns:
            ReportExtractResult with extracted fields.

        Raises:
            ValueError: If the HTML contains no extractable text.
        """
        text = extract_text_from_html(html)
        if not text.strip():
            raise ValueError("HTML contains no extractable text")

        return await self._extract(text, fund_code=fund_code)

    async def _extract(
        self,
        text: str,
        *,
        fund_code: str | None = None,
    ) -> ReportExtractResult:
        """Internal extraction logic shared by PDF and HTML paths.

        Args:
            text: Plain text extracted from the report.
            fund_code: Optional fund code for logging.

        Returns:
            ReportExtractResult with extracted fields.
        """
        # Truncate to avoid token overflow
        truncated_text = _truncate_text(text, self._max_text_length)
        raw_text_length = len(truncated_text)

        # Build prompt
        prompt = USER_PROMPT_TEMPLATE.format(text=truncated_text)

        # Call LLM
        from app.ai.service import LLMResult

        llm_result: LLMResult = await self._llm.call(
            use_case=self.USE_CASE,
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            schema=REPORT_EXTRACT_SCHEMA,
            temperature=0.1,
            max_tokens=2000,
        )

        # Parse result
        parsed: dict[str, Any]
        if isinstance(llm_result.content, dict):
            parsed = llm_result.content
        else:
            log.warning(
                "report_extract.unexpected_content_type",
                content_type=type(llm_result.content).__name__,
                fund_code=fund_code,
            )
            return ReportExtractResult(raw_text_length=raw_text_length)

        log.info(
            "report_extract.success",
            fund_code=fund_code or parsed.get("fund_code"),
            report_period=parsed.get("report_period"),
            fields_extracted=sum(
                1 for k in ["manager_view", "style_description", "market_outlook",
                            "key_holdings_rationale"]
                if parsed.get(k)
            ),
        )

        return ReportExtractResult(
            manager_view=parsed.get("manager_view"),
            style_description=parsed.get("style_description"),
            market_outlook=parsed.get("market_outlook"),
            key_holdings_rationale=parsed.get("key_holdings_rationale"),
            report_period=parsed.get("report_period"),
            fund_code=parsed.get("fund_code"),
            fund_name=parsed.get("fund_name"),
            raw_text_length=raw_text_length,
        )


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "MAX_TEXT_LENGTH",
    "REPORT_EXTRACT_SCHEMA",
    "SYSTEM_PROMPT",
    "USER_PROMPT_TEMPLATE",
    "ReportExtractResult",
    "ReportExtractor",
    "extract_text_from_html",
    "extract_text_from_pdf",
]
