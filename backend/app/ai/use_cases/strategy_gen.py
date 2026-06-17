"""Natural language strategy generation use case.

Uses LLM to generate a strategy configuration JSON from a natural language
description. The generated config is validated against the strategy's
parameter schema and checked for parameter range constraints.

Pipeline:
1. Accept a natural language strategy description
2. Build prompt with available strategy templates and constraints
3. Call LLMService with JSON Schema constraint
4. Validate generated config against strategy parameter schema
5. Check parameter ranges (e.g., lookback must be positive, weights sum to 1)
6. Return validated config for user confirmation

Requirements: 11.15, 11.16
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Valid strategy types
VALID_STRATEGY_TYPES = ("dca", "momentum", "risk_parity", "mean_variance", "timing", "fof")

#: JSON Schema for the LLM output — strategy configuration
STRATEGY_GEN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "strategy_type": {
            "type": "string",
            "enum": list(VALID_STRATEGY_TYPES),
            "description": "策略类型",
        },
        "name": {
            "type": "string",
            "description": "策略名称（简短描述性名称）",
        },
        "params": {
            "type": "object",
            "description": "策略参数（根据策略类型不同而不同）",
        },
        "universe": {
            "type": "object",
            "properties": {
                "fund_codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "基金代码列表",
                },
                "description": {
                    "type": "string",
                    "description": "基金池描述（如用户未指定具体代码）",
                },
            },
            "description": "基金池配置",
        },
        "reasoning": {
            "type": "string",
            "description": "选择该策略类型和参数的理由",
        },
    },
    "required": ["strategy_type", "name", "params", "universe"],
    "additionalProperties": False,
}

#: System prompt for strategy generation
SYSTEM_PROMPT = """你是一个专业的公募基金量化策略设计顾问。你的任务是把用户的自然语言需求，转换成一个可执行、结构化、保守且合理的策略配置 JSON。

你的首要目标：
1. 正确识别用户想要的策略类型。
2. 在给定的参数约束内生成合理配置。
3. 信息不足时使用稳健默认值，而不是激进假设。
4. 不编造不存在的策略类型、参数、基金代码或业务能力。

可用的策略类型及其参数：

1. dca（定投策略）
   - amount: 每期投入金额（必填，正数）
   - frequency: 投资频率，可选 "weekly" / "biweekly" / "monthly"（必填）
   - dca_type: 定投类型，可选 "fixed"（定额）/ "value_averaging"（价值平均）/ "smart"（智能定投）
   - ma_window: 均线窗口天数（智能定投时使用，正整数，默认20）

2. momentum（动量轮动策略）
   - lookback_months: 回看月数（必填，正整数，通常3-12）
   - top_n: 持有基金数量（必填，正整数，通常2-5）
   - rebalance_freq: 调仓频率，可选 "weekly" / "monthly" / "quarterly"（必填）
   - score_factor: 评分因子，可选 "return" / "sharpe" / "information_ratio"

3. risk_parity（风险平价策略）
   - rebalance_freq: 调仓频率，可选 "weekly" / "monthly" / "quarterly"（必填）
   - cov_method: 协方差估计方法，可选 "sample" / "ewm" / "shrinkage"
   - lookback_days: 回看天数（正整数，最小20，默认60）

4. mean_variance（均值-方差优化策略）
   - rebalance_freq: 调仓频率，可选 "weekly" / "monthly" / "quarterly"（必填）
   - risk_free_rate: 无风险利率（非负数，默认0.03）
   - target_return: 目标收益率（数字）
   - max_weight: 单资产最大权重（0-1之间，默认0.4）

5. timing（择时策略）
   - method: 择时方法，可选 "dual_ma" / "macd" / "valuation"（必填）
   - fast_window: 快线窗口（正整数，双均线/MACD使用）
   - slow_window: 慢线窗口（正整数，双均线/MACD使用）

6. fof（FOF策略）
   - factor_weights: 因子权重映射（必填，对象，如 {"sharpe": 0.4, "return": 0.3, "volatility": 0.3}）
   - top_n: 持有基金数量（必填，正整数）
   - rebalance_freq: 调仓频率，可选 "weekly" / "monthly" / "quarterly"（必填）
   - optimization: 优化方法，可选 "equal_weight" / "risk_parity" / "mean_variance"

策略选择原则：
- 用户强调“长期、纪律性、定期投入、懒人投资”，优先考虑 dca。
- 用户强调“选强者、轮动、最近表现最好、动量排名”，优先考虑 momentum。
- 用户强调“风险均衡、控制波动、平衡配置”，优先考虑 risk_parity。
- 用户强调“收益风险权衡、最优权重、组合优化”，优先考虑 mean_variance。
- 用户强调“择时、均线、MACD、估值高低切换”，优先考虑 timing。
- 用户强调“多因子选基、FOF、基金组合优选”，优先考虑 fof。

参数生成原则：
- 若用户没有给出具体参数，使用行业里偏稳健、易解释的默认值。
- 若用户描述偏模糊，不要为了“看起来聪明”而生成激进参数。
- 若用户没有给出具体基金代码，不要编造 fund_codes；改为在 universe.description 中描述基金池特征。
- strategy name 要简洁、专业、可读，优先中文。
- reasoning 重点解释“为什么选这个策略类型”和“为什么采用这些关键参数”。

输出要求：
1. strategy_type: 必须是上述 6 种之一
2. name: 简短专业的策略名称（中文）
3. params: 根据策略类型填写参数，必填参数不能缺失
4. universe: 包含 fund_codes 或 description
5. reasoning: 1-3 句，说明匹配逻辑与参数依据

严格限制：
- 不要输出 JSON 以外的任何说明文字
- 不要编造不存在的策略类型或参数
- 不要把模糊需求强行解释为高频、杠杆或复杂衍生策略
- 若信息不足，优先给出保守、可执行、可验证的配置"""

#: User prompt template
USER_PROMPT_TEMPLATE = """请根据以下自然语言策略需求，生成一个严格符合 JSON Schema 的策略配置。

用户需求：
{description}

生成要求：
1. 只输出结构化结果，不补充额外说明
2. 若用户未提供基金代码，不要编造，放到 universe.description
3. 若需求不完整，请选择最匹配且偏稳健的默认参数
4. reasoning 要简洁说明策略类型选择和关键参数依据"""


# ---------------------------------------------------------------------------
# Parameter range validation
# ---------------------------------------------------------------------------

#: Parameter range constraints per strategy type
PARAM_RANGE_RULES: dict[str, dict[str, dict[str, Any]]] = {
    "dca": {
        "amount": {"min": 100, "max": 10_000_000, "type": "number"},
        "frequency": {"enum": ["weekly", "biweekly", "monthly"]},
        "dca_type": {"enum": ["fixed", "value_averaging", "smart"]},
        "ma_window": {"min": 1, "max": 500, "type": "integer"},
    },
    "momentum": {
        "lookback_months": {"min": 1, "max": 36, "type": "integer"},
        "top_n": {"min": 1, "max": 50, "type": "integer"},
        "rebalance_freq": {"enum": ["weekly", "monthly", "quarterly"]},
        "score_factor": {"enum": ["return", "sharpe", "information_ratio"]},
    },
    "risk_parity": {
        "rebalance_freq": {"enum": ["weekly", "monthly", "quarterly"]},
        "cov_method": {"enum": ["sample", "ewm", "shrinkage"]},
        "lookback_days": {"min": 20, "max": 500, "type": "integer"},
    },
    "mean_variance": {
        "rebalance_freq": {"enum": ["weekly", "monthly", "quarterly"]},
        "risk_free_rate": {"min": 0, "max": 0.2, "type": "number"},
        "target_return": {"min": -0.5, "max": 2.0, "type": "number"},
        "max_weight": {"min": 0.01, "max": 1.0, "type": "number"},
    },
    "timing": {
        "method": {"enum": ["dual_ma", "macd", "valuation"]},
        "fast_window": {"min": 1, "max": 200, "type": "integer"},
        "slow_window": {"min": 1, "max": 500, "type": "integer"},
    },
    "fof": {
        "factor_weights": {"type": "object", "weights_sum_max": 2.0},
        "top_n": {"min": 1, "max": 50, "type": "integer"},
        "rebalance_freq": {"enum": ["weekly", "monthly", "quarterly"]},
        "optimization": {"enum": ["equal_weight", "risk_parity", "mean_variance"]},
    },
}

#: Required parameters per strategy type
REQUIRED_PARAMS: dict[str, list[str]] = {
    "dca": ["amount", "frequency"],
    "momentum": ["lookback_months", "top_n", "rebalance_freq"],
    "risk_parity": ["rebalance_freq"],
    "mean_variance": ["rebalance_freq"],
    "timing": ["method"],
    "fof": ["factor_weights", "top_n", "rebalance_freq"],
}


def validate_param_ranges(
    strategy_type: str,
    params: dict[str, Any],
) -> list[str]:
    """Validate strategy parameters against range constraints.

    Checks:
    1. Required parameters are present
    2. Parameter values are within allowed ranges
    3. Enum parameters have valid values
    4. Factor weights sum constraint (for FOF)

    Args:
        strategy_type: The strategy type identifier.
        params: The parameters dictionary to validate.

    Returns:
        List of validation error messages (empty if valid).
    """
    errors: list[str] = []

    if strategy_type not in VALID_STRATEGY_TYPES:
        errors.append(f"无效的策略类型: {strategy_type}")
        return errors

    # Check required parameters
    required = REQUIRED_PARAMS.get(strategy_type, [])
    for param_name in required:
        if param_name not in params:
            errors.append(f"缺少必填参数: {param_name}")

    # Check parameter ranges
    rules = PARAM_RANGE_RULES.get(strategy_type, {})
    for param_name, value in params.items():
        if param_name not in rules:
            continue  # Allow additional parameters

        rule = rules[param_name]

        # Enum check
        if "enum" in rule:
            if value not in rule["enum"]:
                errors.append(
                    f"参数 {param_name} 的值 '{value}' 不在允许范围内: {rule['enum']}"
                )
            continue

        # Type check
        expected_type = rule.get("type")
        if expected_type == "integer":
            if not isinstance(value, int):
                errors.append(f"参数 {param_name} 应为整数类型，实际为 {type(value).__name__}")
                continue
        elif expected_type == "number":
            if not isinstance(value, (int, float)):
                errors.append(f"参数 {param_name} 应为数字类型，实际为 {type(value).__name__}")
                continue
        elif expected_type == "object":
            if not isinstance(value, dict):
                errors.append(f"参数 {param_name} 应为对象类型，实际为 {type(value).__name__}")
                continue
            # Special: factor_weights sum check
            if "weights_sum_max" in rule and isinstance(value, dict):
                weight_values = [v for v in value.values() if isinstance(v, (int, float))]
                if weight_values:
                    total = sum(weight_values)
                    if total > rule["weights_sum_max"]:
                        errors.append(
                            f"参数 {param_name} 的权重总和 {total:.2f} "
                            f"超过最大值 {rule['weights_sum_max']}"
                        )
            continue

        # Range check
        if isinstance(value, (int, float)):
            if "min" in rule and value < rule["min"]:
                errors.append(
                    f"参数 {param_name} 的值 {value} 小于最小值 {rule['min']}"
                )
            if "max" in rule and value > rule["max"]:
                errors.append(
                    f"参数 {param_name} 的值 {value} 大于最大值 {rule['max']}"
                )

    # Special validation: timing strategy fast_window < slow_window
    if strategy_type == "timing":
        fast = params.get("fast_window")
        slow = params.get("slow_window")
        if (
            fast is not None
            and slow is not None
            and isinstance(fast, int)
            and isinstance(slow, int)
            and fast >= slow
        ):
            errors.append(
                f"择时策略的 fast_window ({fast}) 必须小于 slow_window ({slow})"
            )

    return errors


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class StrategyGenResult:
    """Result of strategy generation from natural language.

    Attributes:
        strategy_type: Generated strategy type.
        name: Generated strategy name.
        params: Strategy parameters dictionary.
        universe: Fund universe configuration.
        reasoning: LLM's reasoning for the choice.
        validation_errors: Any validation errors found.
        is_valid: Whether the generated config passed all validations.
    """

    strategy_type: str
    name: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    universe: dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""
    validation_errors: list[str] = field(default_factory=list)
    is_valid: bool = True


# ---------------------------------------------------------------------------
# Main use case class
# ---------------------------------------------------------------------------


class StrategyGenerator:
    """Generates strategy configurations from natural language descriptions.

    Uses LLM to interpret a user's natural language strategy description
    and produce a validated strategy configuration JSON that can be used
    to create a strategy in the system.

    Args:
        llm_service: The unified LLMService instance.
    """

    USE_CASE = "strategy_gen"

    def __init__(self, llm_service: Any) -> None:
        self._llm = llm_service

    async def generate(
        self,
        description: str,
    ) -> StrategyGenResult:
        """Generate a strategy configuration from natural language.

        Args:
            description: Natural language description of the desired strategy.

        Returns:
            StrategyGenResult with the generated and validated configuration.

        Raises:
            AllProvidersFailedError: If all LLM providers fail (propagated
                from LLMService).
        """
        if not description or not description.strip():
            return StrategyGenResult(
                strategy_type="",
                is_valid=False,
                validation_errors=["策略描述不能为空"],
            )

        # Build prompt
        prompt = USER_PROMPT_TEMPLATE.format(description=description.strip())

        # Call LLM service
        from app.ai.service import LLMResult

        llm_result: LLMResult = await self._llm.call(
            use_case=self.USE_CASE,
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            schema=STRATEGY_GEN_SCHEMA,
            temperature=0.2,
            max_tokens=1000,
        )

        # Extract parsed content
        parsed: dict[str, Any]
        if isinstance(llm_result.content, dict):
            parsed = llm_result.content
        else:
            log.warning(
                "strategy_gen.unexpected_content_type",
                content_type=type(llm_result.content).__name__,
            )
            return StrategyGenResult(
                strategy_type="",
                is_valid=False,
                validation_errors=["LLM 返回了非结构化内容"],
            )

        # Extract fields
        strategy_type = parsed.get("strategy_type", "")
        name = parsed.get("name", "")
        params = parsed.get("params", {})
        universe = parsed.get("universe", {})
        reasoning = parsed.get("reasoning", "")

        # Validate strategy type
        validation_errors: list[str] = []
        if strategy_type not in VALID_STRATEGY_TYPES:
            validation_errors.append(
                f"无效的策略类型: {strategy_type}。"
                f"支持的类型: {', '.join(VALID_STRATEGY_TYPES)}"
            )
        else:
            # Validate parameter ranges
            validation_errors = validate_param_ranges(strategy_type, params)

        is_valid = len(validation_errors) == 0

        if not is_valid:
            log.info(
                "strategy_gen.validation_failed",
                strategy_type=strategy_type,
                errors=validation_errors,
            )
        else:
            log.info(
                "strategy_gen.success",
                strategy_type=strategy_type,
                name=name,
            )

        return StrategyGenResult(
            strategy_type=strategy_type,
            name=name,
            params=params,
            universe=universe,
            reasoning=reasoning,
            validation_errors=validation_errors,
            is_valid=is_valid,
        )


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "PARAM_RANGE_RULES",
    "REQUIRED_PARAMS",
    "STRATEGY_GEN_SCHEMA",
    "SYSTEM_PROMPT",
    "USER_PROMPT_TEMPLATE",
    "VALID_STRATEGY_TYPES",
    "StrategyGenResult",
    "StrategyGenerator",
    "validate_param_ranges",
]
