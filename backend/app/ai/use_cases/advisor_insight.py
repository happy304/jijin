"""LLM 辅助交易建议 — 定性分析增强模块。

利用 LLM 对基金的定性信息进行分析，生成辅助判断：
1. 基金公告解读（分红、暂停申购、基金经理变更等）
2. 持仓变动分析（重仓股变化、行业集中度）
3. 宏观环境判断（辅助 regime 检测）
4. 智能化建议理由生成（替代模板拼接）

设计原则：
- LLM 输出作为辅助参考，不直接决定买卖方向
- 输出结构化 JSON，便于与量化评分融合
- 失败时静默降级（不影响主流程）
- 控制 token 消耗（精简 prompt，缓存结果）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# 输出 Schema
# ---------------------------------------------------------------------------

ADVISOR_INSIGHT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sentiment": {
            "type": "string",
            "enum": ["bullish", "bearish", "neutral"],
            "description": "基于定性信息的情绪判断",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "判断置信度 (0~1)",
        },
        "signal_score": {
            "type": "number",
            "minimum": -1,
            "maximum": 1,
            "description": "定性信号评分 (-1到1)",
        },
        "key_factors": {
            "type": "array",
            "items": {"type": "string"},
            "description": "影响判断的关键因素（最多3条）",
        },
        "risk_alerts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "需要关注的风险点（最多2条）",
        },
        "reasoning": {
            "type": "string",
            "description": "简要推理过程（50字以内）",
        },
    },
    "required": ["sentiment", "confidence", "signal_score",
                 "key_factors", "reasoning"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """你是 GPT-5.4，扮演“中国公募基金交易建议辅助分析师”。

你的职责：
1. 只基于输入中给出的基金公告、持仓摘要、市场环境和量化摘要做判断。
2. 输出给量化交易建议引擎使用的结构化 JSON，不要输出额外文本。
3. 你的判断是“辅助解释层”，不能直接替代引擎的买卖决策。

分析原则：
- 先区分事实、推断与风险，不要把猜测写成事实。
- 信息不足时保持中性，降低 confidence，并让 signal_score 接近 0。
- 不要编造基金公告内容、持仓细节、收益率、经理观点或市场事件。
- 不要使用“稳赚”“必涨”“确定性极高”这类绝对化措辞。
- 分红本身不创造额外价值；暂停大额申购可能是规模控制信号；基金经理变更通常需要观察期。

分析维度：
1. 基金公告事件（分红、暂停申购、经理变更、清盘风险等）
2. 持仓特征（重仓行业、集中度、风格漂移）
3. 市场环境（宏观趋势、行业轮动）
4. 基金运营状态（规模变化、申赎情况）
5. 与量化摘要是否共振或冲突

输出规则：
- sentiment: bullish / bearish / neutral
- confidence: 0~1，只有信息充分且一致时才能明显提高
- signal_score: -1 到 1，表示定性辅助信号强弱
- key_factors: 最多 3 条，写最关键、最可验证的因素
- risk_alerts: 最多 2 条，没有就返回空数组
- reasoning: 50 字以内，必须简洁、克制、可追溯

校准要求：
- 单条普通公告通常不足以给出强烈看多/看空结论。
- 若量化摘要偏强但定性信息偏弱，优先给“谨慎偏多”而非强烈看多。
- 若出现清盘风险、重大负面公告、频繁经理变更等，可适度偏空，但仍需反映不确定性。
- 除非输入证据非常明确，否则 confidence 不应超过 0.75，|signal_score| 不应超过 0.6。"""

USER_PROMPT_TEMPLATE = """请分析以下基金的定性信息并给出投资判断：

基金代码：{fund_code}
基金名称：{fund_name}
基金类型：{fund_type}

{context_sections}

请基于以上信息给出结构化的投资判断。如果信息不足，请保持中性（signal_score接近0）。"""


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class AdvisorInsight:
    """LLM 生成的定性分析结果。"""

    fund_code: str
    sentiment: str = "neutral"  # bullish/bearish/neutral
    confidence: float = 0.0
    signal_score: float = 0.0  # -1 到 1
    key_factors: list[str] = field(default_factory=list)
    risk_alerts: list[str] = field(default_factory=list)
    reasoning: str = ""
    llm_used: bool = False  # 是否实际调用了 LLM
    error: str | None = None


# ---------------------------------------------------------------------------
# 核心类
# ---------------------------------------------------------------------------


class AdvisorInsightGenerator:
    """LLM 辅助交易建议生成器。

    为单只基金生成定性分析，输出结构化的情绪判断和信号评分。
    结果可与量化评分融合，提升建议的"智能感"。

    Args:
        llm_service: LLMService 实例
    """

    USE_CASE = "advisor_insight"

    def __init__(self, llm_service: Any) -> None:
        self._llm = llm_service

    async def analyze(
        self,
        fund_code: str,
        fund_name: str | None = None,
        fund_type: str | None = None,
        announcements: list[dict[str, Any]] | None = None,
        holdings_summary: str | None = None,
        macro_context: str | None = None,
        quantitative_summary: str | None = None,
    ) -> AdvisorInsight:
        """为单只基金生成 LLM 定性分析。

        Args:
            fund_code: 基金代码
            fund_name: 基金名称
            fund_type: 基金类型
            announcements: 最近公告列表 [{title, date, category}]
            holdings_summary: 持仓摘要文本
            macro_context: 宏观环境描述
            quantitative_summary: 量化评分摘要（供 LLM 参考）

        Returns:
            AdvisorInsight
        """
        result = AdvisorInsight(fund_code=fund_code)

        # 构建上下文
        sections = []
        if announcements:
            ann_text = "\n".join(
                f"- [{a.get('date', '?')}] {a.get('title', '?')}"
                for a in announcements[:5]
            )
            sections.append(f"【最近公告】\n{ann_text}")

        if holdings_summary:
            sections.append(f"【持仓特征】\n{holdings_summary}")

        if macro_context:
            sections.append(f"【市场环境】\n{macro_context}")

        if quantitative_summary:
            sections.append(f"【量化评分参考】\n{quantitative_summary}")

        if not sections:
            # 无定性信息可分析
            result.reasoning = "无可用定性信息"
            return result

        context_text = "\n\n".join(sections)
        prompt = USER_PROMPT_TEMPLATE.format(
            fund_code=fund_code,
            fund_name=fund_name or "未知",
            fund_type=fund_type or "未知",
            context_sections=context_text,
        )

        try:
            from app.ai.service import LLMResult

            llm_result: LLMResult = await self._llm.call(
                use_case=self.USE_CASE,
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                schema=ADVISOR_INSIGHT_SCHEMA,
                temperature=0.2,
                max_tokens=500,
                cache_ttl=6 * 3600,  # 缓存 6 小时
            )

            if isinstance(llm_result.content, dict):
                parsed = llm_result.content
                result.sentiment = parsed.get("sentiment", "neutral")
                result.confidence = parsed.get("confidence", 0.0)
                result.signal_score = parsed.get("signal_score", 0.0)
                result.key_factors = parsed.get("key_factors", [])
                result.risk_alerts = parsed.get("risk_alerts", [])
                result.reasoning = parsed.get("reasoning", "")
                result.llm_used = True
            else:
                result.error = "LLM 返回非结构化内容"

        except Exception as e:
            log.warning(
                "advisor_insight.llm_error",
                fund_code=fund_code,
                error=str(e),
            )
            result.error = str(e)

        return result


    async def batch_analyze(
        self,
        funds: list[dict[str, Any]],
    ) -> dict[str, AdvisorInsight]:
        """批量分析多只基金。

        Args:
            funds: [{fund_code, fund_name, fund_type, announcements, ...}]

        Returns:
            {fund_code: AdvisorInsight}
        """
        results: dict[str, AdvisorInsight] = {}
        for fund in funds:
            insight = await self.analyze(
                fund_code=fund["fund_code"],
                fund_name=fund.get("fund_name"),
                fund_type=fund.get("fund_type"),
                announcements=fund.get("announcements"),
                holdings_summary=fund.get("holdings_summary"),
                macro_context=fund.get("macro_context"),
                quantitative_summary=fund.get("quantitative_summary"),
            )
            results[fund["fund_code"]] = insight
        return results


# ---------------------------------------------------------------------------
# 数据库加载辅助
# ---------------------------------------------------------------------------


async def load_fund_announcements(
    fund_codes: list[str],
    session: Any,
    days: int = 30,
) -> dict[str, list[dict[str, Any]]]:
    """加载基金最近公告。

    Returns:
        {fund_code: [{title, date, category}, ...]}
    """
    from datetime import date, timedelta
    from sqlalchemy import text

    result: dict[str, list[dict[str, Any]]] = {}
    if not fund_codes:
        return result

    min_date = date.today() - timedelta(days=days)
    placeholders = ", ".join([f":c{i}" for i in range(len(fund_codes))])

    try:
        query = text(
            f"SELECT fund_code, title, publish_date, category "
            f"FROM fund_announcements "
            f"WHERE fund_code IN ({placeholders}) "
            f"AND publish_date >= :min_date "
            f"ORDER BY fund_code, publish_date DESC"
        )
        params = {f"c{i}": code for i, code in enumerate(fund_codes)}
        params["min_date"] = min_date

        rows = await session.execute(query, params)
        for row in rows:
            code = row[0]
            if code not in result:
                result[code] = []
            if len(result[code]) < 5:  # 每只基金最多5条
                result[code].append({
                    "title": row[1],
                    "date": str(row[2]) if row[2] else None,
                    "category": row[3],
                })
    except Exception:
        pass  # 公告表可能不存在

    return result


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

__all__ = [
    "ADVISOR_INSIGHT_SCHEMA",
    "AdvisorInsight",
    "AdvisorInsightGenerator",
    "load_fund_announcements",
]
