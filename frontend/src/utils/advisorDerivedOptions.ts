import type { AdvisorAnalyzeResponse, AdvisorHistoryDetailResponse } from '@/api/advisor';
import type { StrategySummary } from '@/api/strategies';
import type { FundOptionSummary } from '@/api/funds';
import type { AdvisorPositionItem } from '@/utils/advisorPositions';
import type { AdvisorLastRequestMeta } from '@/utils/advisorRequestPayloads';

export function buildAdvisorStrategyOptions(strategies: StrategySummary[] | undefined) {
  return strategies?.map((strategy) => {
    let fundCount = 0;
    if (Array.isArray(strategy.universe)) {
      fundCount = strategy.universe.length;
    } else if (strategy.universe && typeof strategy.universe === 'object') {
      const codes = (strategy.universe as Record<string, unknown>).fund_codes;
      fundCount = Array.isArray(codes) ? codes.length : 0;
    }
    return {
      value: strategy.id,
      label: `${strategy.name}（${strategy.strategy_type}，${fundCount}只基金）`,
    };
  }) || [];
}

export function extractStrategyFundCodes(
  strategies: StrategySummary[] | undefined,
  selectedStrategyId: number | undefined,
): string[] | undefined {
  if (!selectedStrategyId || !strategies) return undefined;
  const strategy = strategies.find((item) => item.id === selectedStrategyId);
  if (!strategy) return undefined;
  if (Array.isArray(strategy.universe)) return strategy.universe;
  if (strategy.universe && typeof strategy.universe === 'object') {
    const codes = (strategy.universe as Record<string, unknown>).fund_codes;
    if (Array.isArray(codes)) return codes as string[];
  }
  return undefined;
}

export function buildAdvisorHotFundCodes(funds: FundOptionSummary[]): string[] {
  const preferred = ['161725', '005827', '110022', '161005', '001632', '163406', '012414', '000248'];
  const activeCodes = new Set(funds.filter((fund) => fund.status === 'active').map((fund) => fund.code));
  const picked = preferred.filter((code) => activeCodes.has(code));
  if (picked.length >= 6) return picked.slice(0, 6);
  const fallback = funds
    .filter((fund) => fund.status === 'active')
    .slice(0, 12)
    .map((fund) => fund.code)
    .filter((code) => !picked.includes(code));
  return [...picked, ...fallback].slice(0, 6);
}

export function collectAdvisorExtraFundCodes({
  positions,
  selectedFundCodes,
  selectedStrategyFundCodes,
  historyDetail,
  result,
  lastRequestMeta,
}: {
  positions: AdvisorPositionItem[];
  selectedFundCodes?: string[];
  selectedStrategyFundCodes?: string[];
  historyDetail?: AdvisorHistoryDetailResponse | null;
  result?: AdvisorAnalyzeResponse | null;
  lastRequestMeta?: AdvisorLastRequestMeta | null;
}): string[] {
  const codes = new Set<string>();

  positions.forEach((position) => {
    if (position.fund_code) codes.add(position.fund_code);
  });
  selectedFundCodes?.forEach((code) => codes.add(code));
  selectedStrategyFundCodes?.forEach((code) => codes.add(code));
  historyDetail?.fund_codes?.forEach((code) => codes.add(code));
  result?.advices?.forEach((advice) => codes.add(advice.fund_code));
  lastRequestMeta?.fund_codes?.forEach((code) => codes.add(code));

  return Array.from(codes);
}
