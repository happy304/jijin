import type { FundOptionSummary } from '@/api/funds';

export interface AdvisorFundOption {
  value: string;
  label: string;
}

export function buildAdvisorFundOption(code: string, name?: string | null): AdvisorFundOption {
  return {
    value: code,
    label: `${code} - ${name?.trim() || '未命名基金'}`,
  };
}

export function buildAdvisorFundOptions(funds: FundOptionSummary[], extraCodes: string[] = []): AdvisorFundOption[] {
  const optionMap = new Map<string, AdvisorFundOption>();

  funds.forEach((fund) => {
    optionMap.set(fund.code, buildAdvisorFundOption(fund.code, fund.name));
  });

  extraCodes
    .map((code) => code.trim())
    .filter(Boolean)
    .forEach((code) => {
      if (!optionMap.has(code)) {
        optionMap.set(code, buildAdvisorFundOption(code));
      }
    });

  return Array.from(optionMap.values()).sort((a, b) => a.value.localeCompare(b.value));
}
