"""Factor brainstorm use case — LLM-assisted factor discovery.

Uses LLM to generate candidate factor formulas in a restricted DSL based
on a user's research hypothesis. The DSL parser validates that only
allowed data fields and whitelisted functions are used. Validated factors
are submitted for IC/IR testing; insignificant factors are logged as
experiments rather than registered.

Pipeline:
1. Accept research idea/hypothesis from user
2. Build prompt with available data fields and whitelisted functions
3. Call LLMService with JSON Schema constraint
4. Parse and validate each candidate factor's DSL expression
5. Submit validated factors for IC/IR testing
6. Register significant factors; log insignificant ones as experiments

Requirements: 11.20, 11.21, 11.22, 11.23
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# DSL Configuration
# ---------------------------------------------------------------------------

#: Whitelisted functions allowed in factor DSL expressions
WHITELISTED_FUNCTIONS: set[str] = {
    "rolling_mean",
    "rolling_std",
    "rank",
    "zscore",
    "lag",
    "diff",
    "abs",
    "log",
    "sqrt",
    "max",
    "min",
}

#: Available data fields that can be referenced in DSL expressions
AVAILABLE_FIELDS: set[str] = {
    "close",
    "unit_nav",
    "accum_nav",
    "adj_nav",
    "daily_return",
    "volume",
    "turnover",
    "fund_size",
    "management_fee",
    "custodian_fee",
    "top10_weight",
    "hhi",
    "benchmark_return",
}


# ---------------------------------------------------------------------------
# DSL Parser
# ---------------------------------------------------------------------------


class DSLValidationError(Exception):
    """Raised when a DSL expression fails validation."""

    def __init__(self, expression: str, reason: str) -> None:
        self.expression = expression
        self.reason = reason
        super().__init__(f"Invalid DSL expression '{expression}': {reason}")


class _DSLValidator(ast.NodeVisitor):
    """AST visitor that validates a DSL expression against the whitelist.

    Only allows:
    - References to AVAILABLE_FIELDS (as Name nodes)
    - Calls to WHITELISTED_FUNCTIONS
    - Numeric literals
    - Basic arithmetic operators (+, -, *, /, **, unary -)
    - Comparisons (for conditional expressions)
    """

    def __init__(self) -> None:
        self.errors: list[str] = []
        self._allowed_names = AVAILABLE_FIELDS | WHITELISTED_FUNCTIONS

    def visit_Name(self, node: ast.Name) -> None:
        if node.id not in self._allowed_names:
            self.errors.append(
                f"Unknown identifier '{node.id}'. "
                f"Allowed fields: {sorted(AVAILABLE_FIELDS)}, "
                f"Allowed functions: {sorted(WHITELISTED_FUNCTIONS)}"
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Check that the function being called is whitelisted
        if isinstance(node.func, ast.Name):
            if node.func.id not in WHITELISTED_FUNCTIONS:
                self.errors.append(
                    f"Function '{node.func.id}' is not whitelisted. "
                    f"Allowed: {sorted(WHITELISTED_FUNCTIONS)}"
                )
        elif isinstance(node.func, ast.Attribute):
            # Disallow method calls like obj.method()
            self.errors.append(
                f"Attribute access/method calls are not allowed in DSL"
            )
        else:
            self.errors.append("Complex function calls are not allowed in DSL")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.errors.append(
            f"Attribute access '{ast.dump(node)}' is not allowed in DSL"
        )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        self.errors.append("Import statements are not allowed in DSL")

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.errors.append("Import statements are not allowed in DSL")

    # Allow basic operations
    def visit_BinOp(self, node: ast.BinOp) -> None:
        self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # Allow numeric and string constants
        if not isinstance(node.value, (int, float, str, type(None))):
            self.errors.append(
                f"Unsupported constant type: {type(node.value).__name__}"
            )

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self.errors.append("Subscript/indexing is not allowed in DSL")

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.errors.append("Lambda expressions are not allowed in DSL")

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self.errors.append("List comprehensions are not allowed in DSL")

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self.errors.append("Dict comprehensions are not allowed in DSL")

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self.errors.append("Generator expressions are not allowed in DSL")


def validate_dsl_expression(expression: str) -> list[str]:
    """Validate a DSL expression against the whitelist.

    Parses the expression as Python AST and checks that only allowed
    identifiers and functions are used.

    Args:
        expression: The DSL formula string to validate.

    Returns:
        List of validation error messages. Empty list means valid.
    """
    if not expression or not expression.strip():
        return ["Expression is empty"]

    # Basic length check
    if len(expression) > 500:
        return ["Expression too long (max 500 characters)"]

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        return [f"Syntax error: {e.msg}"]

    validator = _DSLValidator()
    validator.visit(tree)
    return validator.errors


def is_valid_dsl(expression: str) -> bool:
    """Check if a DSL expression is valid.

    Args:
        expression: The DSL formula string.

    Returns:
        True if the expression passes all validation checks.
    """
    return len(validate_dsl_expression(expression)) == 0


# ---------------------------------------------------------------------------
# IC/IR Validation
# ---------------------------------------------------------------------------


class FactorSignificance(str, Enum):
    """Result of IC/IR significance testing."""

    SIGNIFICANT = "significant"
    INSIGNIFICANT = "insignificant"
    ERROR = "error"


@dataclass
class ICIRResult:
    """Result of IC/IR validation for a candidate factor.

    Attributes:
        ic_mean: Mean Information Coefficient.
        ic_std: Standard deviation of IC.
        ir: Information Ratio (IC_mean / IC_std).
        significance: Whether the factor is statistically significant.
        p_value: P-value from t-test on IC series.
    """

    ic_mean: float = 0.0
    ic_std: float = 0.0
    ir: float = 0.0
    significance: FactorSignificance = FactorSignificance.INSIGNIFICANT
    p_value: float = 1.0


class ICIRValidator:
    """Validates candidate factors by computing IC/IR metrics.

    This is the interface for IC/IR testing. In production, it would
    compute actual IC against forward returns. For testing purposes,
    it can be mocked.

    Args:
        ic_threshold: Minimum absolute IC mean for significance.
            Defaults to 0.03.
        ir_threshold: Minimum IR for significance. Defaults to 0.5.
    """

    def __init__(
        self,
        *,
        ic_threshold: float = 0.03,
        ir_threshold: float = 0.5,
    ) -> None:
        self._ic_threshold = ic_threshold
        self._ir_threshold = ir_threshold

    async def validate(
        self,
        formula: str,
        name: str,
    ) -> ICIRResult:
        """Run IC/IR validation on a candidate factor formula.

        Args:
            formula: The DSL expression for the factor.
            name: Human-readable name for the factor.

        Returns:
            ICIRResult with computed metrics and significance assessment.
        """
        # In production, this would:
        # 1. Evaluate the DSL expression against historical data
        # 2. Compute cross-sectional IC against forward returns
        # 3. Run t-test on IC series
        # For now, this is a placeholder that should be overridden/mocked
        raise NotImplementedError(
            "ICIRValidator.validate must be implemented with actual data access"
        )

    def assess_significance(self, ic_mean: float, ir: float) -> FactorSignificance:
        """Determine if a factor is statistically significant.

        Args:
            ic_mean: Mean IC value.
            ir: Information Ratio.

        Returns:
            FactorSignificance enum value.
        """
        if abs(ic_mean) >= self._ic_threshold and abs(ir) >= self._ir_threshold:
            return FactorSignificance.SIGNIFICANT
        return FactorSignificance.INSIGNIFICANT


# ---------------------------------------------------------------------------
# DSL Evaluator (links candidate formulas to real data)
# ---------------------------------------------------------------------------

import operator as _operator

# Functions that operate on a single pd.Series (or DataFrame)
def _rolling_mean(field, window):  # type: ignore[no-untyped-def]
    return field.rolling(window=int(window), min_periods=1).mean()


def _rolling_std(field, window):  # type: ignore[no-untyped-def]
    return field.rolling(window=int(window), min_periods=2).std()


def _zscore_xs(field):  # type: ignore[no-untyped-def]
    """Cross-sectional z-score (axis=1: across assets per date)."""
    import pandas as pd  # noqa: F401

    if hasattr(field, "axis") or not hasattr(field, "sub"):
        return field
    mean = field.mean(axis=1)
    std = field.std(axis=1, ddof=1)
    return field.sub(mean, axis=0).div(std.replace(0, float("nan")), axis=0)


def _rank_xs(field):  # type: ignore[no-untyped-def]
    """Cross-sectional percentile rank (axis=1)."""
    import pandas as pd  # noqa: F401

    return field.rank(axis=1, pct=True)


def _lag(field, periods):  # type: ignore[no-untyped-def]
    return field.shift(int(periods))


def _diff(field, periods):  # type: ignore[no-untyped-def]
    return field.diff(int(periods))


def _safe_log(field):  # type: ignore[no-untyped-def]
    import numpy as _np

    return _np.log(field.where(field > 0))


def _safe_sqrt(field):  # type: ignore[no-untyped-def]
    import numpy as _np

    return _np.sqrt(field.where(field >= 0))


_DSL_FUNCTIONS: dict[str, Any] = {
    "rolling_mean": _rolling_mean,
    "rolling_std": _rolling_std,
    "zscore": _zscore_xs,
    "rank": _rank_xs,
    "lag": _lag,
    "diff": _diff,
    "abs": lambda x: x.abs() if hasattr(x, "abs") else abs(x),
    "log": _safe_log,
    "sqrt": _safe_sqrt,
    "max": lambda a, b: a.combine(b, func=max) if hasattr(a, "combine") else max(a, b),
    "min": lambda a, b: a.combine(b, func=min) if hasattr(a, "combine") else min(a, b),
}


def _eval_dsl_node(node: ast.AST, fields: dict[str, Any]) -> Any:
    """Evaluate a validated DSL AST node against a fields dict."""
    import numpy as _np  # noqa: F401

    if isinstance(node, ast.Expression):
        return _eval_dsl_node(node.body, fields)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in fields:
            return fields[node.id]
        if node.id in _DSL_FUNCTIONS:
            return _DSL_FUNCTIONS[node.id]
        raise DSLValidationError(node.id, f"Unknown name '{node.id}'")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise DSLValidationError(
                ast.dump(node), "Only direct function calls allowed"
            )
        fn = _DSL_FUNCTIONS.get(node.func.id)
        if fn is None:
            raise DSLValidationError(
                node.func.id, f"Function '{node.func.id}' not whitelisted"
            )
        args = [_eval_dsl_node(a, fields) for a in node.args]
        return fn(*args)
    if isinstance(node, ast.UnaryOp):
        operand = _eval_dsl_node(node.operand, fields)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return +operand
        raise DSLValidationError(ast.dump(node), "Unsupported unary op")
    if isinstance(node, ast.BinOp):
        left = _eval_dsl_node(node.left, fields)
        right = _eval_dsl_node(node.right, fields)
        ops_map = {
            ast.Add: _operator.add,
            ast.Sub: _operator.sub,
            ast.Mult: _operator.mul,
            ast.Div: _operator.truediv,
            ast.Pow: _operator.pow,
            ast.Mod: _operator.mod,
        }
        op = ops_map.get(type(node.op))
        if op is None:
            raise DSLValidationError(ast.dump(node), "Unsupported binary op")
        return op(left, right)
    raise DSLValidationError(ast.dump(node), "Unsupported AST node")


def evaluate_dsl(formula: str, fields: dict[str, Any]) -> Any:
    """Evaluate a DSL formula against a fields dict.

    Args:
        formula: A DSL expression that has passed ``validate_dsl_expression``.
        fields: Dictionary mapping field names (e.g. 'daily_return',
            'fund_size') to their value (typically a wide pandas DataFrame
            indexed by date with columns being asset codes).

    Returns:
        Evaluation result. For factor formulas this is typically a wide
        DataFrame (date × asset).

    Raises:
        DSLValidationError: If the formula contains disallowed constructs.
    """
    errors = validate_dsl_expression(formula)
    if errors:
        raise DSLValidationError(formula, "; ".join(errors))
    tree = ast.parse(formula, mode="eval")
    return _eval_dsl_node(tree, fields)


class DataDrivenICIRValidator(ICIRValidator):
    """Concrete ICIRValidator that evaluates the DSL against historical data.

    Wires the LLM-generated factor formula into the IC analysis pipeline:

        1. evaluate_dsl(formula, fields) → wide DataFrame of factor values
        2. ic_analysis.evaluate_factor(factor_panel, returns_panel) → IC stats
        3. assess_significance(ic_mean, ir) → SIGNIFICANT / INSIGNIFICANT

    Inputs are pre-loaded by the caller — this class never touches the
    database directly so it stays unit-testable.

    Args:
        fields: Dictionary mapping DSL field names (e.g. 'daily_return',
            'fund_size') to wide DataFrames (date × asset).
        forward_returns: Wide DataFrame of forward returns (typically the
            same shape as the field panels). DSL field 'daily_return' may
            be reused if the formula computes its own forward shift.
        ic_threshold: Minimum |IC mean| for significance.
        ir_threshold: Minimum |IR| for significance.
        method: 'pearson' or 'spearman' (default spearman = Rank IC).
    """

    def __init__(
        self,
        *,
        fields: dict[str, Any],
        forward_returns: Any,
        ic_threshold: float = 0.03,
        ir_threshold: float = 0.5,
        method: str = "spearman",
    ) -> None:
        super().__init__(ic_threshold=ic_threshold, ir_threshold=ir_threshold)
        self._fields = fields
        self._forward_returns = forward_returns
        self._method = method

    async def validate(
        self,
        formula: str,
        name: str,
    ) -> ICIRResult:
        """Evaluate the DSL formula and run a real IC test on it.

        Args:
            formula: The candidate factor's DSL expression.
            name: For logging.

        Returns:
            ICIRResult with real IC/IR/p-value/significance.
        """
        from app.domain.factors.ic_analysis import (
            compute_ic_series,
            compute_ic_stats,
        )

        try:
            factor_panel = evaluate_dsl(formula, self._fields)
        except (DSLValidationError, ZeroDivisionError, KeyError, TypeError) as exc:
            log.warning(
                "ic_ir_validator.dsl_eval_failed",
                name=name,
                formula=formula,
                error=str(exc),
            )
            return ICIRResult(
                ic_mean=0.0,
                ic_std=0.0,
                ir=0.0,
                significance=FactorSignificance.ERROR,
                p_value=1.0,
            )

        # The DSL may return a Series instead of a DataFrame for trivial
        # formulas — accept only DataFrames as factor panels.
        if not hasattr(factor_panel, "columns"):
            log.warning(
                "ic_ir_validator.bad_factor_shape",
                name=name,
                formula=formula,
            )
            return ICIRResult(
                significance=FactorSignificance.ERROR,
                p_value=1.0,
            )

        ic_series = compute_ic_series(
            factor_panel=factor_panel,
            forward_returns=self._forward_returns,
            method=self._method,  # type: ignore[arg-type]
        )
        ic_stats = compute_ic_stats(ic_series, method=self._method)  # type: ignore[arg-type]

        if ic_stats is None:
            return ICIRResult(
                significance=FactorSignificance.ERROR,
                p_value=1.0,
            )

        significance = self.assess_significance(ic_stats.ic_mean, ic_stats.ic_ir)
        return ICIRResult(
            ic_mean=ic_stats.ic_mean,
            ic_std=ic_stats.ic_std,
            ir=ic_stats.ic_ir,
            significance=significance,
            p_value=ic_stats.ic_p_value,
        )


# ---------------------------------------------------------------------------
# LLM Schema & Prompts
# ---------------------------------------------------------------------------

#: JSON Schema for the LLM output
FACTOR_BRAINSTORM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "factors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "A short descriptive name for the factor",
                    },
                    "formula": {
                        "type": "string",
                        "description": "Factor formula using the restricted DSL",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Brief explanation of why this factor might be predictive",
                    },
                },
                "required": ["name", "formula", "rationale"],
            },
            "minItems": 1,
            "maxItems": 10,
        },
    },
    "required": ["factors"],
    "additionalProperties": False,
}

#: System prompt for factor brainstorm
SYSTEM_PROMPT = f"""你是一个量化因子研究专家。你的任务是根据用户的研究想法，生成候选因子公式。

你必须使用以下受限 DSL 来表达因子公式：

可用数据字段：
{', '.join(sorted(AVAILABLE_FIELDS))}

可用函数（白名单）：
{', '.join(sorted(WHITELISTED_FUNCTIONS))}

函数说明：
- rolling_mean(field, window): 滚动均值
- rolling_std(field, window): 滚动标准差
- rank(field): 截面排名（0-1标准化）
- zscore(field): 截面Z-score标准化
- lag(field, periods): 滞后N期
- diff(field, periods): N期差分
- abs(expr): 绝对值
- log(expr): 自然对数
- sqrt(expr): 平方根
- max(expr1, expr2): 取较大值
- min(expr1, expr2): 取较小值

DSL 规则：
1. 只能使用上述字段和函数
2. 支持基本算术运算：+, -, *, /, **
3. 支持数字常量
4. 不允许导入、属性访问、下标索引
5. 公式应该是一个单一表达式

示例因子公式：
- rolling_mean(daily_return, 20) / rolling_std(daily_return, 20)  # 滚动夏普
- diff(adj_nav, 5) / lag(adj_nav, 5)  # 5日动量
- zscore(fund_size) * rank(daily_return)  # 规模动量交叉因子
- rolling_std(daily_return, 60) - rolling_std(daily_return, 20)  # 波动率期限结构

输出要求：
1. 生成 3-5 个候选因子
2. 每个因子必须有名称、公式和理由
3. 公式必须严格遵循 DSL 语法
4. 因子应该有经济学直觉支撑"""

#: User prompt template
USER_PROMPT_TEMPLATE = """研究想法/假设：
{hypothesis}

请基于上述研究想法，生成候选因子公式。输出JSON格式。"""


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CandidateFactor:
    """A single candidate factor generated by LLM.

    Attributes:
        name: Descriptive name for the factor.
        formula: DSL expression for the factor.
        rationale: Why this factor might be predictive.
        is_valid_dsl: Whether the DSL expression passed validation.
        dsl_errors: Validation errors (if any).
        ic_ir_result: IC/IR test result (None if not yet tested).
        registered: Whether the factor was registered in the factor library.
    """

    name: str
    formula: str
    rationale: str
    is_valid_dsl: bool = False
    dsl_errors: list[str] = field(default_factory=list)
    ic_ir_result: ICIRResult | None = None
    registered: bool = False


@dataclass
class BrainstormResult:
    """Result of the factor brainstorm pipeline.

    Attributes:
        hypothesis: The original research hypothesis.
        candidates: List of candidate factors generated.
        valid_count: Number of DSL-valid candidates.
        significant_count: Number of factors that passed IC/IR testing.
        registered_count: Number of factors registered to the library.
        experiment_log: Factors that were tested but not significant.
    """

    hypothesis: str
    candidates: list[CandidateFactor] = field(default_factory=list)
    valid_count: int = 0
    significant_count: int = 0
    registered_count: int = 0
    experiment_log: list[CandidateFactor] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main use case class
# ---------------------------------------------------------------------------


class FactorBrainstormer:
    """Generates and validates candidate factors from research hypotheses.

    Orchestrates the full brainstorm pipeline:
    1. Call LLM to generate candidate factor formulas
    2. Validate each formula against the restricted DSL
    3. Submit valid factors for IC/IR testing
    4. Register significant factors; log others as experiments

    Args:
        llm_service: The unified LLMService instance.
        ic_ir_validator: Optional ICIRValidator for significance testing.
            If None, IC/IR testing is skipped.
        auto_register: Whether to automatically register significant factors.
            Defaults to True.
    """

    USE_CASE = "factor_brainstorm"

    def __init__(
        self,
        llm_service: Any,
        *,
        ic_ir_validator: ICIRValidator | None = None,
        auto_register: bool = True,
    ) -> None:
        self._llm = llm_service
        self._ic_ir_validator = ic_ir_validator
        self._auto_register = auto_register

    async def brainstorm(
        self,
        hypothesis: str,
    ) -> BrainstormResult:
        """Run the full factor brainstorm pipeline.

        Args:
            hypothesis: The user's research idea or hypothesis.

        Returns:
            BrainstormResult with all candidates and their validation status.

        Raises:
            AllProvidersFailedError: If all LLM providers fail.
        """
        if not hypothesis or not hypothesis.strip():
            raise ValueError("Research hypothesis cannot be empty")

        # Step 1: Call LLM to generate candidates
        prompt = USER_PROMPT_TEMPLATE.format(hypothesis=hypothesis)

        from app.ai.service import LLMResult

        llm_result: LLMResult = await self._llm.call(
            use_case=self.USE_CASE,
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            schema=FACTOR_BRAINSTORM_SCHEMA,
            temperature=0.3,  # Slightly higher for creativity
            max_tokens=2000,
        )

        # Step 2: Parse LLM output
        parsed: dict[str, Any]
        if isinstance(llm_result.content, dict):
            parsed = llm_result.content
        else:
            log.warning(
                "factor_brainstorm.unexpected_content_type",
                content_type=type(llm_result.content).__name__,
            )
            return BrainstormResult(hypothesis=hypothesis)

        raw_factors = parsed.get("factors", [])
        result = BrainstormResult(hypothesis=hypothesis)

        # Step 3: Validate each candidate's DSL
        for raw in raw_factors:
            name = raw.get("name", "unnamed")
            formula = raw.get("formula", "")
            rationale = raw.get("rationale", "")

            dsl_errors = validate_dsl_expression(formula)
            candidate = CandidateFactor(
                name=name,
                formula=formula,
                rationale=rationale,
                is_valid_dsl=len(dsl_errors) == 0,
                dsl_errors=dsl_errors,
            )
            result.candidates.append(candidate)

        result.valid_count = sum(1 for c in result.candidates if c.is_valid_dsl)

        # Step 4: Submit valid factors for IC/IR testing
        if self._ic_ir_validator:
            for candidate in result.candidates:
                if not candidate.is_valid_dsl:
                    continue
                try:
                    ic_ir = await self._ic_ir_validator.validate(
                        formula=candidate.formula,
                        name=candidate.name,
                    )
                    candidate.ic_ir_result = ic_ir

                    if ic_ir.significance == FactorSignificance.SIGNIFICANT:
                        result.significant_count += 1
                        # Step 5: Register significant factors
                        if self._auto_register:
                            self._register_factor(candidate)
                            candidate.registered = True
                            result.registered_count += 1
                    else:
                        # Log as experiment
                        result.experiment_log.append(candidate)
                        log.info(
                            "factor_brainstorm.insignificant",
                            name=candidate.name,
                            formula=candidate.formula,
                            ic_mean=ic_ir.ic_mean,
                            ir=ic_ir.ir,
                        )
                except Exception as e:
                    log.warning(
                        "factor_brainstorm.ic_ir_error",
                        name=candidate.name,
                        error=str(e),
                    )
                    candidate.ic_ir_result = ICIRResult(
                        significance=FactorSignificance.ERROR
                    )

        log.info(
            "factor_brainstorm.complete",
            hypothesis=hypothesis[:100],
            total_candidates=len(result.candidates),
            valid=result.valid_count,
            significant=result.significant_count,
            registered=result.registered_count,
        )

        return result

    def _register_factor(self, candidate: CandidateFactor) -> None:
        """Register a significant factor in the factor registry.

        Adds the factor with LLM source attribution metadata.

        Args:
            candidate: The validated and significant candidate factor.
        """
        from app.domain.factors.registry import _FACTOR_REGISTRY, FactorDef

        # Create a safe factor name (snake_case)
        safe_name = re.sub(r"[^a-z0-9_]", "_", candidate.name.lower())
        safe_name = re.sub(r"_+", "_", safe_name).strip("_")

        # Avoid name collisions
        if safe_name in _FACTOR_REGISTRY:
            safe_name = f"llm_{safe_name}"

        # Register with source attribution
        factor_def = FactorDef(
            name=safe_name,
            category="llm_generated",
            description=f"LLM-generated: {candidate.rationale}. Formula: {candidate.formula}",
        )
        _FACTOR_REGISTRY[safe_name] = factor_def

        log.info(
            "factor_brainstorm.registered",
            name=safe_name,
            formula=candidate.formula,
            source="llm",
        )


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "AVAILABLE_FIELDS",
    "BrainstormResult",
    "CandidateFactor",
    "DSLValidationError",
    "DataDrivenICIRValidator",
    "FACTOR_BRAINSTORM_SCHEMA",
    "FactorBrainstormer",
    "FactorSignificance",
    "ICIRResult",
    "ICIRValidator",
    "SYSTEM_PROMPT",
    "USER_PROMPT_TEMPLATE",
    "WHITELISTED_FUNCTIONS",
    "evaluate_dsl",
    "is_valid_dsl",
    "validate_dsl_expression",
]
