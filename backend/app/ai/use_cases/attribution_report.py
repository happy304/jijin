"""智能归因报告用例 — 基于已计算数据生成自然语言分析报告。

核心设计约束：
- LLM 只负责**解释**已计算好的数值，**禁止**自行计算任何数值
- 输入必须是已计算完成的 Brinson 归因 + Fama-French 归因 + 绩效指标
- 输出带"AI 生成内容"标签 + 原始数据链接
- Prompt 明确禁止 LLM 计算数值、推测未提供信息、给出投资建议

Pipeline:
1. 接收已计算的归因数据和绩效指标（结构化 dict）
2. 构建 prompt，明确约束 LLM 行为
3. 调用 LLMService 获取自然语言分析
4. 包装输出，附加"AI 生成内容"标签和原始数据链接

Requirements: 11.17, 11.18, 11.19
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: System prompt — 明确禁止 LLM 计算数值
SYSTEM_PROMPT = (
    "你是一位专业的基金绩效分析师。你的任务是基于已经计算好的归因数据和绩效指标，"
    "撰写一段专业、客观的中文分析报告。\n\n"
    "严格要求：\n"
    "1. 只解释已给数据，不要推测未提供的信息，不要给出投资建议\n"
    "2. 不要自行计算任何数值，所有数字必须直接引用输入数据\n"
    "3. 不要编造或推测任何未在输入中出现的数据\n"
    "4. 使用专业但易懂的语言，适合有一定金融知识的读者\n"
    "5. 报告长度控制在 300-500 字\n"
    "6. 结构清晰，分段阐述收益表现、风险特征、归因分析"
)

#: User prompt template
USER_PROMPT_TEMPLATE = """基于以下已计算的归因数据和绩效指标，撰写一段专业分析报告。

【策略名称】
{strategy_name}

【收益指标】
{return_metrics}

【风险指标】
{risk_metrics}

【风险调整指标】
{risk_adjusted_metrics}

【基准对比指标】
{benchmark_metrics}

【Fama-French 归因】
{fama_french_data}

【Brinson 归因】
{brinson_data}

要求：
1. 只解释已给数据，不要推测未提供的信息，不要给出投资建议
2. 不要自行计算任何数值
3. 分析应涵盖：收益表现概述、风险特征、因子暴露解读、配置与选股贡献
4. 如果某项数据为空或不可用，跳过该部分，不要编造"""

#: AI 生成内容标签
AI_GENERATED_LABEL = "⚠️ AI 生成内容 — 仅供参考，不构成投资建议"

#: 原始数据链接模板
DATA_LINK_TEMPLATE = "/api/v1/backtests/{run_id}/attribution"


# ---------------------------------------------------------------------------
# Input dataclass
# ---------------------------------------------------------------------------


@dataclass
class AttributionReportInput:
    """归因报告的输入数据 — 全部为已计算完成的数值。

    Attributes:
        strategy_name: 策略名称。
        run_id: 回测运行 ID（用于生成原始数据链接）。
        return_metrics: 收益类指标字典。
        risk_metrics: 风险类指标字典。
        risk_adjusted_metrics: 风险调整指标字典。
        benchmark_metrics: 基准对比指标字典。
        fama_french: Fama-French 归因结果字典（可选）。
        brinson: Brinson 归因结果字典（可选）。
    """

    strategy_name: str = ""
    run_id: str = ""
    return_metrics: dict[str, Any] = field(default_factory=dict)
    risk_metrics: dict[str, Any] = field(default_factory=dict)
    risk_adjusted_metrics: dict[str, Any] = field(default_factory=dict)
    benchmark_metrics: dict[str, Any] = field(default_factory=dict)
    fama_french: dict[str, Any] | None = None
    brinson: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Output dataclass
# ---------------------------------------------------------------------------


@dataclass
class AttributionReportOutput:
    """归因报告输出。

    Attributes:
        report_text: LLM 生成的自然语言分析报告。
        ai_generated_label: AI 生成内容标签。
        data_link: 原始数据链接。
        input_data: 输入的原始数据（供前端展示参考）。
    """

    report_text: str = ""
    ai_generated_label: str = AI_GENERATED_LABEL
    data_link: str = ""
    input_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 可传输的字典。"""
        return {
            "report_text": self.report_text,
            "ai_generated_label": self.ai_generated_label,
            "data_link": self.data_link,
            "input_data": self.input_data,
        }


# ---------------------------------------------------------------------------
# Main use case class
# ---------------------------------------------------------------------------


class AttributionReportGenerator:
    """智能归因报告生成器。

    接收已计算的归因数据和绩效指标，调用 LLM 生成自然语言分析报告。
    LLM 仅负责解释数据，不做任何计算。

    Args:
        llm_service: LLMService 实例。
    """

    USE_CASE = "attribution_report"

    def __init__(self, llm_service: Any) -> None:
        self._llm = llm_service

    async def generate(
        self,
        input_data: AttributionReportInput,
    ) -> AttributionReportOutput:
        """生成归因分析报告。

        Args:
            input_data: 已计算完成的归因数据和绩效指标。

        Returns:
            AttributionReportOutput 包含报告文本、AI 标签和数据链接。

        Raises:
            AllProvidersFailedError: 所有 LLM provider 失败时抛出。
        """
        # 构建 prompt
        prompt = self._build_prompt(input_data)

        # 调用 LLM — 纯文本输出，不需要 JSON Schema
        from app.ai.service import LLMResult

        llm_result: LLMResult = await self._llm.call(
            use_case=self.USE_CASE,
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            schema=None,  # 纯文本输出
            temperature=0.3,
            max_tokens=1000,
        )

        # 提取报告文本
        report_text: str
        if isinstance(llm_result.content, str):
            report_text = llm_result.content
        else:
            # 不应发生，但安全处理
            report_text = str(llm_result.content)

        # 构建数据链接
        data_link = (
            DATA_LINK_TEMPLATE.format(run_id=input_data.run_id)
            if input_data.run_id
            else ""
        )

        # 构建输入数据摘要（供前端展示）
        raw_input = {
            "strategy_name": input_data.strategy_name,
            "return_metrics": input_data.return_metrics,
            "risk_metrics": input_data.risk_metrics,
            "risk_adjusted_metrics": input_data.risk_adjusted_metrics,
            "benchmark_metrics": input_data.benchmark_metrics,
            "fama_french": input_data.fama_french,
            "brinson": input_data.brinson,
        }

        log.info(
            "attribution_report.generated",
            strategy_name=input_data.strategy_name,
            run_id=input_data.run_id,
            report_length=len(report_text),
        )

        return AttributionReportOutput(
            report_text=report_text,
            ai_generated_label=AI_GENERATED_LABEL,
            data_link=data_link,
            input_data=raw_input,
        )

    def _build_prompt(self, input_data: AttributionReportInput) -> str:
        """构建用户 prompt，将已计算数据格式化为文本。"""
        return USER_PROMPT_TEMPLATE.format(
            strategy_name=input_data.strategy_name or "未命名策略",
            return_metrics=_format_metrics(input_data.return_metrics),
            risk_metrics=_format_metrics(input_data.risk_metrics),
            risk_adjusted_metrics=_format_metrics(input_data.risk_adjusted_metrics),
            benchmark_metrics=_format_metrics(input_data.benchmark_metrics),
            fama_french_data=_format_metrics(input_data.fama_french),
            brinson_data=_format_metrics(input_data.brinson),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_metrics(data: dict[str, Any] | None) -> str:
    """将指标字典格式化为可读文本。

    None 值的字段显示为"不可用"。
    """
    if not data:
        return "（无数据）"

    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, dict):
            # 嵌套字典（如 betas、allocation_effect）
            nested = ", ".join(
                f"{k}: {_format_value(v)}" for k, v in value.items()
            )
            lines.append(f"  {key}: {{{nested}}}")
        elif isinstance(value, list):
            lines.append(f"  {key}: {value}")
        else:
            lines.append(f"  {key}: {_format_value(value)}")
    return "\n".join(lines)


def _format_value(value: Any) -> str:
    """格式化单个值。"""
    if value is None:
        return "不可用"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "AI_GENERATED_LABEL",
    "AttributionReportGenerator",
    "AttributionReportInput",
    "AttributionReportOutput",
    "DATA_LINK_TEMPLATE",
    "SYSTEM_PROMPT",
    "USER_PROMPT_TEMPLATE",
]
