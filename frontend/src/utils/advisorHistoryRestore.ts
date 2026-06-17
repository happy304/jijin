import type { AdvisorHistoryDetailResponse } from '@/api/advisor';
import type { AdvisorPositionItem } from '@/utils/advisorPositions';

function buildProfileFormValues(detail: AdvisorHistoryDetailResponse) {
  return {
    total_capital: detail.total_capital,
    risk_level: detail.risk_level,
    investment_goal: detail.user_profile?.investment_goal,
    investment_horizon: detail.user_profile?.investment_horizon,
    liquidity_need: detail.user_profile?.liquidity_need,
    max_drawdown_tolerance: detail.user_profile?.max_drawdown_tolerance,
    monthly_invest_amount: detail.user_profile?.monthly_invest_amount,
    industry_concentration_tolerance: detail.user_profile?.industry_concentration_tolerance,
    qdii_fx_risk_tolerance: detail.user_profile?.qdii_fx_risk_tolerance,
    fee_sensitivity: detail.user_profile?.fee_sensitivity,
    compare_risk_levels: Boolean(detail.user_profile?.compare_risk_levels ?? detail.risk_comparison),
  };
}

export function buildPositionsFromHistoryDetail(detail: AdvisorHistoryDetailResponse): AdvisorPositionItem[] | null {
  if (detail.positions_detail && Object.keys(detail.positions_detail).length > 0) {
    return Object.entries(detail.positions_detail).map(([code, info]) => ({
      fund_code: code,
      market_value: info.market_value || detail.current_positions?.[code] || 0,
      shares: info.shares || info.amount || 0,
      buy_date: info.buy_date || '',
      cost_basis: info.cost_basis || info.cost || 0,
    }));
  }

  if (detail.current_positions && Object.keys(detail.current_positions).length > 0) {
    return Object.entries(detail.current_positions).map(([code, market_value]) => ({
      fund_code: code,
      market_value,
      shares: 0,
      buy_date: '',
      cost_basis: 0,
    }));
  }

  return null;
}

export function buildStrategyFormValuesFromHistoryDetail(detail: AdvisorHistoryDetailResponse) {
  return {
    strategy_id: detail.strategy_id,
    ...buildProfileFormValues(detail),
  };
}

export function buildManualFormValuesFromHistoryDetail(detail: AdvisorHistoryDetailResponse) {
  return {
    fund_codes: detail.fund_codes,
    ...buildProfileFormValues(detail),
  };
}
