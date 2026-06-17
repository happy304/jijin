import { Alert, Card, Col, Row, Statistic, Typography } from 'antd';

const { Text } = Typography;

type AlertType = 'success' | 'warning' | 'error' | 'info';

export interface TrustNoticeSummary {
  type: AlertType;
  message: string;
  description: string;
}

export interface DataFreshnessNoticeProps {
  lastDate?: string | null;
  style?: React.CSSProperties;
}

export interface ResultInterpretationNoticeProps {
  navDataStale?: unknown | null;
  navQualityWarning?: unknown | null;
  tradingDays?: number | null;
  totalTrades?: number | null;
  style?: React.CSSProperties;
}

export interface SampleScopeCardProps {
  startDate?: string | null;
  endDate?: string | null;
  tradingDays?: number | null;
  totalTrades?: number | null;
  style?: React.CSSProperties;
}

export function dataQualityStatusLabel(status: string | null | undefined): string {
  const map: Record<string, string> = {
    good: '良好',
    warning: '警告',
    poor: '较差',
    unknown: '未知',
  };
  return map[status || ''] || String(status || '-');
}

export function dataQualityTagColor(status: string | null | undefined): string {
  if (status === 'good') return 'green';
  if (status === 'warning') return 'orange';
  if (status === 'poor') return 'red';
  return 'default';
}

export function dataQualityAlertType(status: string | null | undefined): AlertType {
  if (status === 'good') return 'success';
  if (status === 'warning') return 'warning';
  if (status === 'poor') return 'error';
  return 'info';
}

export function dataQualitySummary(status: string | null | undefined): string {
  if (status === 'good') return '数据质量良好，可用于个人研究、筛选、回测和组合检查。';
  if (status === 'warning') return '数据存在轻微缺口或异常，关键分析前建议先核对净值和复权口径。';
  if (status === 'poor') return '数据质量较差，暂不建议直接用于回测或组合检查，请优先补齐或复核数据。';
  return '数据质量状态未知，建议先完成数据采集和质量检查。';
}

export function buildNavFreshnessSummary(lastDate: string | null | undefined): TrustNoticeSummary {
  if (!lastDate) {
    return {
      type: 'info',
      message: '暂无最新净值日期',
      description: '当前无法判断数据新鲜度，建议先完成净值采集后再进行深入分析。',
    };
  }

  const latest = new Date(lastDate);
  const today = new Date();
  const latestDay = new Date(latest.getFullYear(), latest.getMonth(), latest.getDate());
  const todayDay = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const lagDays = Math.max(
    Math.floor((todayDay.getTime() - latestDay.getTime()) / (1000 * 60 * 60 * 24)),
    0,
  );

  if (lagDays >= 10) {
    return {
      type: 'error',
      message: `净值数据已滞后 ${lagDays} 天`,
      description: '当前分析可能依赖过期净值，建议先更新数据，再判断收益、回撤和组合适配。',
    };
  }
  if (lagDays >= 5) {
    return {
      type: 'warning',
      message: `净值数据可能滞后 ${lagDays} 天`,
      description: '关键筛选、回测或组合检查前，建议确认最新净值和复权口径是否已更新。',
    };
  }
  return {
    type: 'success',
    message: lagDays === 0 ? '净值数据已更新至今日' : `净值数据更新于 ${lagDays} 天前`,
    description: '当前数据新鲜度适合继续做个人研究，但仍建议结合基金类型和样本区间审慎解读。',
  };
}

export function buildResultInterpretationSummary({
  navDataStale,
  navQualityWarning,
  tradingDays,
  totalTrades,
}: Omit<ResultInterpretationNoticeProps, 'style'>): TrustNoticeSummary {
  if (navDataStale) {
    return {
      type: 'warning',
      message: '当前结果依赖的底层净值口径已更新',
      description: '建议优先重新运行或刷新分析，再对收益率、Sharpe 和最大回撤做结论判断。',
    };
  }
  if (navQualityWarning) {
    return {
      type: 'warning',
      message: '当前结果涉及 NAV 数据质量提示',
      description: '请先核对受影响基金的数据覆盖、缺口和异常跳变，再解读策略表现。',
    };
  }
  if ((tradingDays ?? 0) > 0 && (tradingDays ?? 0) < 60) {
    return {
      type: 'warning',
      message: '样本区间较短',
      description: '当前交易日样本偏少，收益和风险指标更容易受短期市场波动影响。',
    };
  }
  if ((totalTrades ?? 0) > 0 && (totalTrades ?? 0) < 5) {
    return {
      type: 'info',
      message: '交易次数较少',
      description: '请避免仅凭少量交易结果推断策略稳定性，建议结合滚动指标和更长区间观察。',
    };
  }
  return {
    type: 'success',
    message: '当前样本与数据提示未见明显异常',
    description: '仍需结合样本区间、指标口径、交易成本和未来市场变化独立判断，不应将结果视为投资建议。',
  };
}

export function DataFreshnessNotice({ lastDate, style }: DataFreshnessNoticeProps) {
  const summary = buildNavFreshnessSummary(lastDate);
  return (
    <Alert
      type={summary.type}
      showIcon
      style={style}
      message={summary.message}
      description={summary.description}
    />
  );
}

export function ResultInterpretationNotice(props: ResultInterpretationNoticeProps) {
  const summary = buildResultInterpretationSummary(props);
  return (
    <Alert
      type={summary.type}
      showIcon
      style={props.style}
      message={summary.message}
      description={summary.description}
    />
  );
}

export function SampleScopeCard({
  startDate,
  endDate,
  tradingDays,
  totalTrades,
  style,
}: SampleScopeCardProps) {
  return (
    <Card size="small" title="样本与口径提示" style={style}>
      <Row gutter={[12, 12]}>
        <Col xs={24} sm={12} md={6}>
          <Statistic title="分析起始" value={startDate || '-'} valueStyle={{ fontSize: 18 }} />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Statistic title="分析结束" value={endDate || '-'} valueStyle={{ fontSize: 18 }} />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Statistic title="交易日样本" value={tradingDays ?? '-'} />
        </Col>
        <Col xs={24} sm={12} md={6}>
          <Statistic title="交易次数" value={totalTrades ?? '-'} />
        </Col>
      </Row>
      <Text type="secondary" style={{ display: 'block', marginTop: 12 }}>
        结果会随净值复权口径、样本区间、费用假设和交易次数变化而变化；请先确认样本充分，再解读收益与风险指标。
      </Text>
    </Card>
  );
}
