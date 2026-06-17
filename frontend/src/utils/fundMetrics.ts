import type { FundDetail, NavResponse } from '@/api/funds';
import type { FeeTableItem, MetricTableItem } from '@/components/FundMetricsSummary';

export function buildPerformanceMetrics(navData: NavResponse | undefined): MetricTableItem[] {
  if (!navData?.records?.length) return [];

  const records = navData.records.filter((record) => record.unit_nav !== null);
  if (records.length < 2) {
    return [{ key: 'insufficientData', label: '绩效指标', value: '数据不足' }];
  }

  const firstNav = parseFloat(records[0].unit_nav!);
  const lastNav = parseFloat(records[records.length - 1].unit_nav!);
  if (!Number.isFinite(firstNav) || !Number.isFinite(lastNav) || firstNav <= 0) {
    return [{ key: 'insufficientData', label: '绩效指标', value: '数据不足' }];
  }
  const totalReturn = ((lastNav - firstNav) / firstNav) * 100;

  const dailyReturns = records
    .filter((record) => record.daily_return !== null)
    .map((record) => parseFloat(record.daily_return!));

  const avgDailyReturn = dailyReturns.length > 0
    ? dailyReturns.reduce((a, b) => a + b, 0) / dailyReturns.length
    : null;

  const volatility = dailyReturns.length > 1 && avgDailyReturn != null
    ? Math.sqrt(
      dailyReturns.reduce(
        (sum, value) => sum + Math.pow(value - avgDailyReturn, 2),
        0,
      ) / (dailyReturns.length - 1),
    ) * Math.sqrt(252)
    : null;

  let maxDrawdown = 0;
  let peak = firstNav;
  for (const record of records) {
    const nav = parseFloat(record.unit_nav!);
    if (!Number.isFinite(nav) || nav <= 0) continue;
    if (nav > peak) peak = nav;
    const drawdown = (nav - peak) / peak;
    if (drawdown < maxDrawdown) maxDrawdown = drawdown;
  }

  return [
    { key: 'totalReturn', label: '区间收益率', value: `${totalReturn.toFixed(2)}%` },
    { key: 'annualizedVol', label: '年化波动率', value: volatility == null ? '数据不足' : `${(volatility * 100).toFixed(2)}%` },
    { key: 'maxDrawdown', label: '最大回撤幅度', value: `${Math.abs(maxDrawdown * 100).toFixed(2)}%` },
    { key: 'latestNav', label: '最新净值', value: lastNav.toFixed(4) },
    { key: 'dataPoints', label: '数据点数', value: `${records.length}` },
  ];
}

export function buildFeeData(fund: FundDetail | undefined): FeeTableItem[] {
  if (!fund) return [];

  const fees: FeeTableItem[] = [];
  if (fund.management_fee) {
    fees.push({
      key: 'management',
      type: '管理费',
      rate: `${(parseFloat(fund.management_fee) * 100).toFixed(2)}%/年`,
    });
  }
  if (fund.custodian_fee) {
    fees.push({
      key: 'custodian',
      type: '托管费',
      rate: `${(parseFloat(fund.custodian_fee) * 100).toFixed(2)}%/年`,
    });
  }
  return fees;
}
