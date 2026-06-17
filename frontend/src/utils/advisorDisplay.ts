import type { AdvisorPositionImportGovernanceSummary, TradingAdviceItem } from '@/api/advisor';

export function formatPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '-';
  return `${(value * 100).toFixed(2)}%`;
}

export function formatMaybePct(value: unknown): string {
  return typeof value === 'number' ? formatPct(value) : '-';
}

export function formatAuditValue(value: unknown): string {
  if (value == null) return '-';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'number') return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(4);
  return String(value);
}

export function compactHash(value: string | null | undefined): string {
  if (!value) return '-';
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

export function overfitRiskLabel(level: string | null | undefined): string {
  const map: Record<string, string> = {
    low: '低',
    medium: '中',
    high: '高',
    unknown: '未知',
  };
  return map[level || ''] || String(level || '-');
}

export function overfitRiskTagColor(level: string | null | undefined): string {
  if (level === 'low') return 'green';
  if (level === 'medium') return 'orange';
  if (level === 'high') return 'red';
  return 'default';
}

export function signalSourceLabel(source: string): string {
  const map: Record<string, string> = {
    technical: '技术',
    momentum: '动量',
    strategy: '策略',
    prediction: '预测',
    cross_sectional: '截面',
  };
  return map[source] || source;
}

export function reliabilityStatusLabel(status: string | null | undefined): string {
  const map: Record<string, string> = {
    healthy: '健康',
    degraded: '减弱',
    unhealthy: '失效',
    insufficient_data: '样本不足',
    unknown: '未知',
    not_evaluated: '未评估',
  };
  return map[status || ''] || String(status || '-');
}

export function oosSelectionSourceLabel(source: string | null | undefined): string {
  const map: Record<string, string> = {
    exact: '命中当前风险档',
    moderate_fallback: '回退到稳健档缓存',
    latest_fallback: '回退到最近可用缓存',
  };
  return map[source || ''] || '来源未知';
}

export function baselineNameLabel(name: string | null | undefined): string {
  const map: Record<string, string> = {
    dca: '定投',
    risk_parity: '风险平价',
    simple_momentum: '简单动量',
  };
  return map[name || ''] || String(name || '-');
}

export function formatDateWithWeekday(value: string | null | undefined): string {
  if (!value) return '-';
  const date = new Date(`${value}T00:00:00+08:00`);
  if (Number.isNaN(date.getTime())) return value;
  return `${value}（${date.toLocaleDateString('zh-CN', { weekday: 'short' })}）`;
}

export function formatRequestTime(value: string | null | undefined): string {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' });
}

export function tradeIntentLabel(intent: string | null | undefined): string {
  if (intent === 'subscribe') return '申购';
  if (intent === 'redeem') return '赎回';
  return '持有';
}

export function executionStatusLabel(status: string | null | undefined): string {
  const map: Record<string, string> = {
    planned: '计划执行',
    executed: '已执行',
    partial: '部分执行',
    not_executed: '未执行',
    no_record: '未记录',
  };
  return map[status || ''] || String(status || '-');
}

export function executionStatusColor(status: string | null | undefined): string {
  if (status === 'executed') return 'green';
  if (status === 'partial') return 'blue';
  if (status === 'planned') return 'gold';
  if (status === 'not_executed') return 'red';
  return 'default';
}

export function executionSummaryStatusLabel(status: string | null | undefined): string {
  const map: Record<string, string> = {
    no_actionable_advice: '无待复核候选',
    no_execution_records: '尚未记录执行',
    fully_adopted: '全部采纳',
    partially_adopted: '部分采纳',
    not_adopted: '未采纳',
    unmatched_records: '记录待核对',
  };
  return map[status || ''] || String(status || '-');
}

const ADVICE_ACTION_TEXT: Record<string, string> = {
  buy: '可关注增配',
  sell: '可关注减配',
  hold: '继续观察',
  watch: '观察',
};

export function adviceActionLabel(action: string): string {
  return ADVICE_ACTION_TEXT[action] || action || '-';
}

const SUPPORT_ACTION_TEXT: Record<string, string> = {
  consider_increase: '可考虑增配候选',
  consider_reduce: '可考虑减配候选',
  observe: '维持观察',
  review_required: '需要复核',
};

export function supportActionLabel(action: string | null | undefined): string {
  return SUPPORT_ACTION_TEXT[action || ''] || String(action || '-');
}

export function supportActionTagColor(action: string | null | undefined): string {
  if (action === 'consider_increase') return 'volcano';
  if (action === 'consider_reduce') return 'cyan';
  if (action === 'review_required') return 'orange';
  return 'blue';
}

export function advisorDecisionLabel(advice: Pick<TradingAdviceItem, 'action' | 'support_action' | 'support_label'>): string {
  return advice.support_label || supportActionLabel(advice.support_action) || adviceActionLabel(advice.action);
}

export function adviceStrengthLabel(action: string, confidence: number | null | undefined, strength?: string | null): string {
  const normalized = String(strength || '').toLowerCase();
  if (normalized === 'strong') return action === 'watch' ? '强观察' : '强';
  if (normalized === 'medium') return action === 'watch' ? '中观察' : '中';
  if (normalized === 'weak') return action === 'watch' ? '弱观察' : '弱';
  const value = Number(confidence || 0);
  if (action === 'hold' || action === 'watch') {
    if (value >= 0.75) return '强观察';
    if (value >= 0.45) return '中观察';
    return '弱观察';
  }
  if (value >= 0.75) return '强';
  if (value >= 0.45) return '中';
  return '弱';
}

export function adviceStrengthTagColor(action: string, confidence: number | null | undefined, strength?: string | null): string {
  const label = adviceStrengthLabel(action, confidence, strength);
  if (action === 'hold' || action === 'watch') return 'blue';
  if (label.startsWith('强')) return action === 'buy' ? 'red' : 'green';
  if (label.startsWith('中')) return action === 'buy' ? 'volcano' : 'cyan';
  return 'default';
}

export function riskLevelLabel(level: string | null | undefined): string {
  const map: Record<string, string> = {
    conservative: '保守型',
    moderate: '稳健型',
    aggressive: '进取型',
  };
  return map[level || ''] || String(level || '-');
}

export function riskLevelTagColor(level: string | null | undefined): string {
  if (level === 'conservative') return 'green';
  if (level === 'aggressive') return 'red';
  return 'blue';
}

export function investmentGoalLabel(value: string | null | undefined): string {
  const map: Record<string, string> = {
    cash_management: '现金管理',
    stable_growth: '稳健增值',
    balanced: '均衡配置',
    long_term_growth: '长期成长',
  };
  return map[value || ''] || String(value || '-');
}

export function investmentHorizonLabel(value: string | null | undefined): string {
  const map: Record<string, string> = {
    within_3_months: '3个月以内',
    '3_to_12_months': '3-12个月',
    '1_to_3_years': '1-3年',
    over_3_years: '3年以上',
  };
  return map[value || ''] || String(value || '-');
}

export function liquidityNeedLabel(value: string | null | undefined): string {
  const map: Record<string, string> = {
    high: '高流动性',
    medium: '中等流动性',
    low: '低流动性',
  };
  return map[value || ''] || String(value || '-');
}

export function toleranceLabel(value: string | null | undefined): string {
  const map: Record<string, string> = {
    low: '低',
    medium: '中',
    high: '高',
  };
  return map[value || ''] || String(value || '-');
}

export function evaluationLabelText(value: string | null | undefined): string {
  const map: Record<string, string> = {
    effective: '有效',
    neutral: '中性',
    ineffective: '失效',
    not_evaluable: '暂不可评估',
  };
  return map[value || ''] || String(value || '-');
}

export function evaluationLabelColor(value: string | null | undefined): string {
  if (value === 'effective') return 'green';
  if (value === 'ineffective') return 'red';
  if (value === 'neutral') return 'blue';
  return 'default';
}

export function driftLevelLabel(level: string | null | undefined): string {
  const map: Record<string, string> = {
    aligned: '金额匹配',
    moderate_deviation: '中等偏离',
    large_deviation: '大幅偏离',
    adopted_without_amount: '已执行未填金额',
    unknown: '未知',
  };
  return map[level || ''] || String(level || '-');
}

export function driftLevelColor(level: string | null | undefined): string {
  if (level === 'aligned') return 'green';
  if (level === 'moderate_deviation') return 'orange';
  if (level === 'large_deviation') return 'red';
  if (level === 'adopted_without_amount') return 'blue';
  return 'default';
}

export function importHistoryStatusLabel(status: string | null | undefined): string {
  const map: Record<string, string> = {
    completed: '全部成功',
    partial: '部分成功',
    failed: '全部失败',
  };
  return map[status || ''] || String(status || '-');
}

export function importHistoryStatusColor(status: string | null | undefined): string {
  if (status === 'completed') return 'green';
  if (status === 'partial') return 'orange';
  if (status === 'failed') return 'red';
  return 'default';
}

export function getImportGovernanceSummary(metadata: Record<string, unknown> | null | undefined): AdvisorPositionImportGovernanceSummary | null {
  const summary = metadata?.governance_summary;
  if (!summary || typeof summary !== 'object') return null;
  return summary as AdvisorPositionImportGovernanceSummary;
}

export function hasImportGovernanceWarnings(summary: AdvisorPositionImportGovernanceSummary | null | undefined): boolean {
  if (!summary) return false;
  return (summary.warnings?.length || 0) > 0
    || (summary.duplicate_fund_codes?.length || 0) > 0
    || (summary.zero_value_fund_codes?.length || 0) > 0
    || (summary.suspicious_cost_fund_codes?.length || 0) > 0;
}

export function formatCurrency(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '-';
  return `¥${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}
