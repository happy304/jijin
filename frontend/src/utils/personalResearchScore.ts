import type { FundDetail, FundHoldingsResponse, NavQualityResponse, NavResponse } from '@/api/funds';
import { dataQualityStatusLabel } from '@/components/DataTrustNotice';
import type { PersonalResearchScore } from '@/components/PersonalResearchScoreCard';

function clampScore(value: number): number {
  if (!Number.isFinite(value)) return 50;
  return Math.max(0, Math.min(100, value));
}

export function buildPersonalResearchScore(
  fund: FundDetail,
  navData: NavResponse | undefined,
  navQuality: NavQualityResponse | undefined,
  holdingsData: FundHoldingsResponse | undefined,
): PersonalResearchScore {
  const records = (navData?.records || []).filter((item) => item.unit_nav != null);
  const explanations: string[] = [];

  if (records.length < 60) {
    return {
      total: 0,
      level: 'insufficient',
      dimensions: [
        { key: 'data', label: '数据基础', score: 0, weight: 100, reason: `有效 NAV 点数 ${records.length}，不足以形成稳定评分。` },
      ],
      explanations: ['当前可用净值样本不足，建议先完成数据采集后再进行个人研究评分。'],
    };
  }

  const firstNav = parseFloat(records[0].unit_nav!);
  const lastNav = parseFloat(records[records.length - 1].unit_nav!);
  const totalReturn = firstNav > 0 ? (lastNav - firstNav) / firstNav : 0;
  const dailyReturns = records
    .filter((item) => item.daily_return != null)
    .map((item) => parseFloat(item.daily_return!))
    .filter((value) => Number.isFinite(value));
  const avgDailyReturn = dailyReturns.length
    ? dailyReturns.reduce((sum, value) => sum + value, 0) / dailyReturns.length
    : 0;
  const volatility = dailyReturns.length > 1
    ? Math.sqrt(dailyReturns.reduce((sum, value) => sum + Math.pow(value - avgDailyReturn, 2), 0) / (dailyReturns.length - 1)) * Math.sqrt(252)
    : 0;
  const positiveRatio = dailyReturns.length
    ? dailyReturns.filter((value) => value > 0).length / dailyReturns.length
    : 0.5;

  let maxDrawdown = 0;
  let peak = firstNav;
  for (const record of records) {
    const nav = parseFloat(record.unit_nav!);
    if (nav > peak) peak = nav;
    if (peak > 0) maxDrawdown = Math.max(maxDrawdown, (peak - nav) / peak);
  }

  const managementFee = fund.management_fee ? parseFloat(fund.management_fee) : 0;
  const custodianFee = fund.custodian_fee ? parseFloat(fund.custodian_fee) : 0;
  const totalFee = managementFee + custodianFee;
  const top10Concentration = holdingsData?.top10_concentration;

  const returnScore = clampScore(50 + totalReturn * 120);
  const riskScore = clampScore(100 - maxDrawdown * 220 - volatility * 60);
  const stabilityScore = clampScore(positiveRatio * 100 - Math.max(0, volatility - 0.15) * 80);
  const costScore = totalFee > 0 ? clampScore(100 - totalFee * 2500) : 60;
  const qualityScore = navQuality?.status === 'good'
    ? 90
    : navQuality?.status === 'warning'
      ? 60
      : navQuality?.status === 'poor'
        ? 30
        : 50;
  const fitScore = top10Concentration == null ? 60 : clampScore(100 - top10Concentration * 90);

  const dimensions = [
    { key: 'return', label: '收益质量', score: returnScore, weight: 25, reason: `近一年区间收益约 ${(totalReturn * 100).toFixed(2)}%。` },
    { key: 'risk', label: '风险控制', score: riskScore, weight: 25, reason: `最大回撤约 ${(maxDrawdown * 100).toFixed(2)}%，年化波动约 ${(volatility * 100).toFixed(2)}%。` },
    { key: 'stability', label: '稳定性', score: stabilityScore, weight: 15, reason: `正收益交易日占比约 ${(positiveRatio * 100).toFixed(1)}%。` },
    { key: 'cost', label: '成本', score: costScore, weight: 15, reason: totalFee > 0 ? `管理费与托管费合计约 ${(totalFee * 100).toFixed(2)}%/年。` : '费率信息缺失，按中性偏保守处理。' },
    { key: 'quality', label: '数据质量', score: qualityScore, weight: 10, reason: `NAV 数据质量状态：${dataQualityStatusLabel(navQuality?.status)}。` },
    { key: 'fit', label: '组合适配', score: fitScore, weight: 10, reason: top10Concentration == null ? '暂无持仓集中度信息，按中性处理。' : `前十大持仓集中度约 ${(top10Concentration * 100).toFixed(2)}%。` },
  ];

  const total = clampScore(dimensions.reduce((sum, item) => sum + item.score * item.weight / 100, 0));
  const level: PersonalResearchScore['level'] = total >= 75 ? 'focus' : total >= 55 ? 'watch' : 'caution';

  if (navQuality?.status === 'poor') explanations.push('数据质量较差，评分仅作参考，建议先复核 NAV 和复权口径。');
  if (maxDrawdown >= 0.25) explanations.push('历史最大回撤较高，需确认是否符合自身风险承受能力。');
  if (top10Concentration != null && top10Concentration >= 0.6) explanations.push('持仓集中度较高，纳入组合前需关注行业/主题集中风险。');
  if (totalFee > 0.015) explanations.push('费率成本偏高，长期持有前建议与同类基金比较。');
  if (explanations.length === 0) explanations.push('当前评分未发现突出的单项警示，但仍需结合数据日期、基金类型和组合目标独立判断。');

  return { total, level, dimensions, explanations };
}
