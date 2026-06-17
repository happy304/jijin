import type {
  AdvisorAnalyzeResponse,
  AdvisorAnalyzeRequest,
  PortfolioAdviceRequest,
  RiskLevel,
} from '@/api/advisor';
import {
  buildAdvisorPositionsDetail,
  buildAdvisorPositionsMap,
  type AdvisorPositionItem,
} from '@/utils/advisorPositions';

export type AdvisorAnalyzeFormValues = AdvisorAnalyzeRequest & {
  fund_codes: string[];
  total_capital: number;
  risk_level: RiskLevel;
};

export type AdvisorStrategyAnalyzeFormValues = PortfolioAdviceRequest & {
  strategy_id: number;
  total_capital: number;
  risk_level: RiskLevel;
};

export interface AdvisorLastRequestMeta {
  fund_codes: string[];
  strategy_id?: number;
  strategy_name?: string;
}

export function buildManualLastRequestMeta(values: AdvisorAnalyzeFormValues): AdvisorLastRequestMeta {
  return { fund_codes: values.fund_codes };
}

export function buildStrategyLastRequestMeta({
  values,
  result,
  strategyLabel,
}: {
  values: AdvisorStrategyAnalyzeFormValues;
  result: AdvisorAnalyzeResponse & { strategy_id?: number; strategy_name?: string };
  strategyLabel?: string;
}): AdvisorLastRequestMeta {
  return {
    fund_codes: result.advices.map((advice) => advice.fund_code),
    strategy_id: values.strategy_id,
    strategy_name: result.strategy_name || strategyLabel || undefined,
  };
}

export function buildAdvisorAnalyzeRequest(
  values: AdvisorAnalyzeFormValues,
  positions: AdvisorPositionItem[],
): AdvisorAnalyzeRequest {
  return {
    fund_codes: values.fund_codes,
    total_capital: values.total_capital,
    risk_level: values.risk_level,
    investment_goal: values.investment_goal,
    investment_horizon: values.investment_horizon,
    liquidity_need: values.liquidity_need,
    max_drawdown_tolerance: values.max_drawdown_tolerance,
    monthly_invest_amount: values.monthly_invest_amount,
    industry_concentration_tolerance: values.industry_concentration_tolerance,
    qdii_fx_risk_tolerance: values.qdii_fx_risk_tolerance,
    fee_sensitivity: values.fee_sensitivity,
    compare_risk_levels: values.compare_risk_levels,
    current_positions: buildAdvisorPositionsMap(positions),
    positions_detail: buildAdvisorPositionsDetail(positions),
  };
}

export function buildAdvisorPortfolioRequest(
  values: AdvisorStrategyAnalyzeFormValues,
  positions: AdvisorPositionItem[],
): PortfolioAdviceRequest {
  return {
    strategy_id: values.strategy_id,
    total_capital: values.total_capital,
    risk_level: values.risk_level,
    investment_goal: values.investment_goal,
    investment_horizon: values.investment_horizon,
    liquidity_need: values.liquidity_need,
    max_drawdown_tolerance: values.max_drawdown_tolerance,
    monthly_invest_amount: values.monthly_invest_amount,
    industry_concentration_tolerance: values.industry_concentration_tolerance,
    qdii_fx_risk_tolerance: values.qdii_fx_risk_tolerance,
    fee_sensitivity: values.fee_sensitivity,
    compare_risk_levels: values.compare_risk_levels,
    current_positions: buildAdvisorPositionsMap(positions),
    positions_detail: buildAdvisorPositionsDetail(positions),
  };
}
