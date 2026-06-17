import type { AdvisorAnalyzeResponse, SaveAdvisorResultRequest } from '@/api/advisor';
import type { AdvisorLastRequestMeta } from '@/utils/advisorRequestPayloads';
import {
  buildAdvisorPositionsDetail,
  buildAdvisorPositionsMap,
  type AdvisorPositionItem,
} from '@/utils/advisorPositions';

export interface AdvisorUserProfileFallback {
  investment_goal?: unknown;
  investment_horizon?: unknown;
  liquidity_need?: unknown;
  max_drawdown_tolerance?: unknown;
  monthly_invest_amount?: unknown;
  industry_concentration_tolerance?: unknown;
  qdii_fx_risk_tolerance?: unknown;
  fee_sensitivity?: unknown;
  compare_risk_levels?: unknown;
}

export function buildAdvisorUserProfileFallback(
  manualValues: Record<string, unknown>,
  strategyValues: Record<string, unknown>,
): AdvisorUserProfileFallback {
  return {
    investment_goal: manualValues.investment_goal || strategyValues.investment_goal,
    investment_horizon: manualValues.investment_horizon || strategyValues.investment_horizon,
    liquidity_need: manualValues.liquidity_need || strategyValues.liquidity_need,
    max_drawdown_tolerance: manualValues.max_drawdown_tolerance ?? strategyValues.max_drawdown_tolerance,
    monthly_invest_amount: manualValues.monthly_invest_amount ?? strategyValues.monthly_invest_amount,
    industry_concentration_tolerance: manualValues.industry_concentration_tolerance || strategyValues.industry_concentration_tolerance,
    qdii_fx_risk_tolerance: manualValues.qdii_fx_risk_tolerance || strategyValues.qdii_fx_risk_tolerance,
    fee_sensitivity: manualValues.fee_sensitivity || strategyValues.fee_sensitivity,
    compare_risk_levels: manualValues.compare_risk_levels ?? strategyValues.compare_risk_levels ?? false,
  };
}

export function buildAdvisorSaveResultPayload({
  result,
  lastRequestMeta,
  positions,
  userProfileFallback,
}: {
  result: AdvisorAnalyzeResponse;
  lastRequestMeta?: AdvisorLastRequestMeta | null;
  positions: AdvisorPositionItem[];
  userProfileFallback: AdvisorUserProfileFallback;
}): SaveAdvisorResultRequest {
  return {
    advice_date: result.advice_date,
    fund_codes: [...(lastRequestMeta?.fund_codes || result.advices.map((advice) => advice.fund_code))].sort(),
    total_capital: result.total_capital,
    risk_level: result.risk_level,
    strategy_id: lastRequestMeta?.strategy_id || null,
    strategy_name: lastRequestMeta?.strategy_name || null,
    current_positions: buildAdvisorPositionsMap(positions),
    positions_detail: buildAdvisorPositionsDetail(positions),
    user_profile: result.user_profile || {
      risk_level: result.risk_level,
      investment_goal: userProfileFallback.investment_goal,
      investment_horizon: userProfileFallback.investment_horizon,
      liquidity_need: userProfileFallback.liquidity_need,
      max_drawdown_tolerance: userProfileFallback.max_drawdown_tolerance,
      monthly_invest_amount: userProfileFallback.monthly_invest_amount,
      industry_concentration_tolerance: userProfileFallback.industry_concentration_tolerance,
      qdii_fx_risk_tolerance: userProfileFallback.qdii_fx_risk_tolerance,
      fee_sensitivity: userProfileFallback.fee_sensitivity,
      compare_risk_levels: userProfileFallback.compare_risk_levels ?? false,
    },
    advices: result.advices as unknown as Record<string, unknown>[],
    summary: result.summary as unknown as Record<string, unknown>,
  };
}

export function hasHighRiskAdvisorAdvice(result: AdvisorAnalyzeResponse | null): boolean {
  return !!result?.advices?.some((advice) => (
    (advice.action === 'buy' || advice.action === 'sell')
    && (
      advice.strength === 'strong'
      || advice.overfit_risk?.level === 'high'
      || advice.data_quality?.status === 'poor'
      || advice.data_quality?.status === 'warning'
      || advice.suitability?.matched === false
    )
  ));
}
