import { useParams, useNavigate } from 'react-router-dom';
import {
  Typography,
  Card,
  Row,
  Col,
  Statistic,
  Spin,
  Alert,
  Button,
  Tag,
  Descriptions,
  Progress,
  Space,
} from 'antd';
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
  ClockCircleOutlined,
  RiseOutlined,
  FallOutlined,
} from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import {
  useSimulation,
  useSimulationPaths,
  type SimulationNavDataStaleWarning,
  type SimulationNavQualityWarning,
} from '@/api/simulations';

const { Title, Text } = Typography;

const SIMULATION_DETAIL_NOTICE = '模拟预测结果仅用于个人研究中的压力测试和情景观察，不代表未来表现或收益承诺，也不构成投资建议或交易指令。请结合数据质量、样本区间和模型假设谨慎解读。';

export function SimulationDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const runId = id ? parseInt(id, 10) : null;

  const { data: simulation, isLoading, isError, error } = useSimulation(runId);
  const { data: pathsData } = useSimulationPaths(
    runId,
    simulation?.status === 'done',
  );

  if (isLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 100 }}>
        <Spin size="large" tip="加载模拟详情..." />
      </div>
    );
  }

  if (isError || !simulation) {
    return (
      <div style={{ padding: 24 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/simulations')}>
          返回列表
        </Button>
        <Alert
          type="error"
          message="加载失败"
          description={error instanceof Error ? error.message : '获取模拟详情时发生错误'}
          showIcon
          style={{ marginTop: 16 }}
        />
      </div>
    );
  }

  const metrics = simulation.metrics;
  const extended = metrics?.extended;
  const isRunning = simulation.status === 'running' || simulation.status === 'pending';

  // Fan chart options
  const fanChartOption = pathsData ? buildFanChartOption(pathsData) : null;

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/simulations')}>
          返回列表
        </Button>
        <Title level={4} style={{ margin: 0 }}>
          模拟预测 #{simulation.id}
          {simulation.strategy_name && ` — ${simulation.strategy_name}`}
        </Title>
        <StatusTag status={simulation.status} />
      </Space>

      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="模拟预测解读提示"
        description={SIMULATION_DETAIL_NOTICE}
      />

      <NavDataWarnings
        navDataStale={simulation.nav_data_stale}
        navQualityWarning={simulation.nav_quality_warning}
      />

      {/* Progress bar for running simulations */}
      {isRunning && (
        <Card style={{ marginBottom: 16 }}>
          <Progress
            percent={simulation.progress ?? 0}
            status="active"
            strokeColor={{ from: '#108ee9', to: '#87d068' }}
          />
          <Text type="secondary">
            {simulation.progress_message || '模拟计算中，请稍候...'}
          </Text>
        </Card>
      )}

      {/* Configuration */}
      <Card title="模拟配置" style={{ marginBottom: 16 }}>
        <Descriptions column={{ xs: 1, sm: 2, md: 4 }} size="small">
          <Descriptions.Item label="模拟方法">
            <Tag color="blue">{simulation.method.toUpperCase()}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="预测期限">
            {simulation.horizon_days} 交易日 (≈{Math.round(simulation.horizon_days / 252 * 12)} 个月)
          </Descriptions.Item>
          <Descriptions.Item label="模拟路径数">
            {simulation.num_simulations.toLocaleString()}
          </Descriptions.Item>
          <Descriptions.Item label="初始资金">
            ¥{Number(simulation.initial_capital || 100000).toLocaleString()}
          </Descriptions.Item>
          {simulation.target_return != null && (
            <Descriptions.Item label="目标收益率">
              {(simulation.target_return * 100).toFixed(1)}%
            </Descriptions.Item>
          )}
        </Descriptions>
      </Card>

      {/* Results */}
      {simulation.status === 'done' && metrics && (
        <>
          {/* Key metrics */}
          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            <Col xs={12} sm={8} md={6}>
              <Card>
                <Statistic
                  title="预期年化收益"
                  value={metrics.expected_return * 100}
                  precision={2}
                  suffix="%"
                  valueStyle={{ color: metrics.expected_return >= 0 ? '#cf1322' : '#3f8600' }}
                  prefix={metrics.expected_return >= 0 ? <RiseOutlined /> : <FallOutlined />}
                />
              </Card>
            </Col>
            <Col xs={12} sm={8} md={6}>
              <Card>
                <Statistic
                  title="中位数收益"
                  value={metrics.median_return * 100}
                  precision={2}
                  suffix="%"
                  valueStyle={{ color: metrics.median_return >= 0 ? '#cf1322' : '#3f8600' }}
                />
              </Card>
            </Col>
            <Col xs={12} sm={8} md={6}>
              <Card>
                <Statistic
                  title="预测波动率"
                  value={metrics.volatility * 100}
                  precision={2}
                  suffix="%"
                />
              </Card>
            </Col>
            <Col xs={12} sm={8} md={6}>
              <Card>
                <Statistic
                  title="VaR (95%)"
                  value={(metrics.var?.['95'] ?? 0) * 100}
                  precision={2}
                  suffix="%"
                  valueStyle={{ color: '#cf1322' }}
                />
              </Card>
            </Col>
            <Col xs={12} sm={8} md={6}>
              <Card>
                <Statistic
                  title="CVaR (95%)"
                  value={(metrics.cvar?.['95'] ?? 0) * 100}
                  precision={2}
                  suffix="%"
                  valueStyle={{ color: '#cf1322' }}
                />
              </Card>
            </Col>
            <Col xs={12} sm={8} md={6}>
              <Card>
                <Statistic
                  title="最大回撤 (P50)"
                  value={metrics.max_drawdown_median * 100}
                  precision={2}
                  suffix="%"
                  valueStyle={{ color: '#cf1322' }}
                />
              </Card>
            </Col>
            {metrics.target_probability != null && (
              <Col xs={12} sm={8} md={6}>
                <Card>
                  <Statistic
                    title={`达成 ${((metrics.target_return ?? 0) * 100).toFixed(0)}% 目标概率`}
                    value={metrics.target_probability * 100}
                    precision={1}
                    suffix="%"
                    valueStyle={{
                      color: metrics.target_probability >= 0.5 ? '#3f8600' : '#faad14',
                    }}
                  />
                </Card>
              </Col>
            )}
            <Col xs={12} sm={8} md={6}>
              <Card>
                <Statistic
                  title="终值中位数"
                  value={metrics.terminal_wealth_median}
                  precision={0}
                  prefix="¥"
                />
              </Card>
            </Col>
          </Row>

          {/* Fan chart */}
          {fanChartOption && (
            <Card title="净值预测扇形图" style={{ marginBottom: 16 }}>
              <ReactECharts option={fanChartOption} style={{ height: 400 }} />
              <Text type="secondary" style={{ display: 'block', textAlign: 'center', marginTop: 8 }}>
                阴影区域从外到内分别为 5%-95%、10%-90%、25%-75% 置信区间，实线为中位数路径
              </Text>
            </Card>
          )}

          {/* Extended metrics */}
          {extended && (
            <Card title="扩展风险指标" style={{ marginBottom: 16 }}>
              <Row gutter={[16, 16]}>
                <Col xs={12} sm={8} md={6}>
                  <Statistic title="预测 Sharpe" value={extended.predicted_sharpe} precision={3} />
                </Col>
                <Col xs={12} sm={8} md={6}>
                  <Statistic title="预测 Sortino" value={extended.predicted_sortino} precision={3} />
                </Col>
                <Col xs={12} sm={8} md={6}>
                  <Statistic title="预测 Calmar" value={extended.predicted_calmar} precision={3} />
                </Col>
                <Col xs={12} sm={8} md={6}>
                  <Statistic
                    title="正收益概率"
                    value={extended.prob_positive_return * 100}
                    precision={1}
                    suffix="%"
                    valueStyle={{ color: extended.prob_positive_return >= 0.5 ? '#3f8600' : '#cf1322' }}
                  />
                </Col>
                <Col xs={12} sm={8} md={6}>
                  <Statistic
                    title="亏损>10% 概率"
                    value={extended.prob_loss_gt_10pct * 100}
                    precision={1}
                    suffix="%"
                    valueStyle={{ color: '#cf1322' }}
                  />
                </Col>
                <Col xs={12} sm={8} md={6}>
                  <Statistic
                    title="亏损>20% 概率"
                    value={extended.prob_loss_gt_20pct * 100}
                    precision={1}
                    suffix="%"
                    valueStyle={{ color: '#cf1322' }}
                  />
                </Col>
                <Col xs={12} sm={8} md={6}>
                  <Statistic title="偏度" value={extended.skewness} precision={3} />
                </Col>
                <Col xs={12} sm={8} md={6}>
                  <Statistic title="超额峰度" value={extended.kurtosis} precision={3} />
                </Col>
              </Row>
            </Card>
          )}

          {/* Terminal wealth distribution */}
          <Card title="终值分布" style={{ marginBottom: 16 }}>
            <Descriptions column={{ xs: 1, sm: 2, md: 4 }} size="small">
              <Descriptions.Item label="均值">
                ¥{metrics.terminal_wealth_mean.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </Descriptions.Item>
              <Descriptions.Item label="中位数">
                ¥{metrics.terminal_wealth_median.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </Descriptions.Item>
              <Descriptions.Item label="5% 分位（悲观）">
                ¥{metrics.terminal_wealth_p5.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </Descriptions.Item>
              <Descriptions.Item label="95% 分位（乐观）">
                ¥{metrics.terminal_wealth_p95.toLocaleString(undefined, { maximumFractionDigits: 0 })}
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </>
      )}

      {/* Error display */}
      {simulation.status === 'failed' && simulation.error_msg && (
        <Alert
          type="error"
          message="模拟失败"
          description={simulation.error_msg}
          showIcon
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helper components
// ---------------------------------------------------------------------------

function StatusTag({ status }: { status: string }) {
  const config: Record<string, { color: string; icon: React.ReactNode; text: string }> = {
    pending: { color: 'default', icon: <ClockCircleOutlined />, text: '等待中' },
    running: { color: 'processing', icon: <SyncOutlined spin />, text: '运行中' },
    done: { color: 'success', icon: <CheckCircleOutlined />, text: '已完成' },
    failed: { color: 'error', icon: <CloseCircleOutlined />, text: '失败' },
  };
  const c = config[status] || config.pending;
  return (
    <Tag color={c.color} icon={c.icon}>
      {c.text}
    </Tag>
  );
}

function NavDataWarnings({
  navDataStale,
  navQualityWarning,
}: {
  navDataStale?: SimulationNavDataStaleWarning | null;
  navQualityWarning?: SimulationNavQualityWarning | null;
}) {
  if (!navDataStale && !navQualityWarning) return null;

  const staleMessage =
    navDataStale?.message || '底层 NAV 复权口径已有更新，当前模拟结果可能已过期，建议重新运行。';
  const qualityMessage =
    navQualityWarning?.message || '部分 NAV 数据存在口径混用或质量提示，请谨慎解读模拟结果。';
  const affectedFunds = navQualityWarning?.funds
    ? Object.keys(navQualityWarning.funds).join('、')
    : '';

  return (
    <Space direction="vertical" style={{ width: '100%', marginBottom: 16 }}>
      {navDataStale && <Alert type="warning" message="净值数据已更新" description={staleMessage} showIcon />}
      {navQualityWarning && (
        <Alert
          type="warning"
          message="NAV 数据质量提示"
          description={affectedFunds ? `${qualityMessage} 受影响基金：${affectedFunds}` : qualityMessage}
          showIcon
        />
      )}
    </Space>
  );
}

// ---------------------------------------------------------------------------
// Fan chart builder
// ---------------------------------------------------------------------------

function buildFanChartOption(pathsData: {
  horizon_days: number;
  initial_capital: number;
  paths: Record<string, number[]>;
}) {
  const { paths, horizon_days } = pathsData;
  const xData = Array.from({ length: horizon_days + 1 }, (_, i) => i);

  // Build area bands: 5-95, 10-90, 25-75 (outer to inner)
  const bands = [
    { lower: 'p5', upper: 'p95', color: 'rgba(64, 158, 255, 0.1)', name: '5%-95%' },
    { lower: 'p10', upper: 'p90', color: 'rgba(64, 158, 255, 0.2)', name: '10%-90%' },
    { lower: 'p25', upper: 'p75', color: 'rgba(64, 158, 255, 0.3)', name: '25%-75%' },
  ];

  const series: Array<Record<string, unknown>> = [];

  // Each band is rendered as two stacked lines:
  // 1. Lower boundary (invisible base)
  // 2. Band height (upper - lower) with colored area fill
  for (const band of bands) {
    const lowerData = paths[band.lower] || [];
    const upperData = paths[band.upper] || [];

    // Lower boundary (invisible, serves as stack base)
    series.push({
      name: `_${band.name}_base`,
      type: 'line',
      data: lowerData,
      lineStyle: { opacity: 0 },
      areaStyle: { opacity: 0 },
      symbol: 'none',
      stack: band.name,
      silent: true,
      tooltip: { show: false },
    });

    // Band height (difference between upper and lower)
    series.push({
      name: band.name,
      type: 'line',
      data: upperData.map((v, i) => Math.max(0, v - (lowerData[i] || 0))),
      lineStyle: { opacity: 0 },
      areaStyle: { color: band.color },
      symbol: 'none',
      stack: band.name,
      silent: true,
    });
  }

  // Median line (on top of all bands)
  series.push({
    name: '中位数 (P50)',
    type: 'line',
    data: paths['p50'] || [],
    lineStyle: { color: '#1890ff', width: 2 },
    symbol: 'none',
    z: 10,
  });

  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params: unknown) => {
        const items = params as Array<{ axisValue: string | number }>;
        const day = items[0]?.axisValue;
        const dayIdx = typeof day === 'string' ? parseInt(day) : (day as number);
        const median = paths['p50']?.[dayIdx];
        const p5 = paths['p5']?.[dayIdx];
        const p95 = paths['p95']?.[dayIdx];
        const p25 = paths['p25']?.[dayIdx];
        const p75 = paths['p75']?.[dayIdx];
        return [
          `<b>第 ${dayIdx} 天</b>`,
          `中位数: ¥${median?.toLocaleString(undefined, { maximumFractionDigits: 0 }) ?? '-'}`,
          `25%-75%: ¥${p25?.toLocaleString(undefined, { maximumFractionDigits: 0 }) ?? '-'} ~ ¥${p75?.toLocaleString(undefined, { maximumFractionDigits: 0 }) ?? '-'}`,
          `5%-95%: ¥${p5?.toLocaleString(undefined, { maximumFractionDigits: 0 }) ?? '-'} ~ ¥${p95?.toLocaleString(undefined, { maximumFractionDigits: 0 }) ?? '-'}`,
        ].join('<br/>');
      },
    },
    legend: {
      data: ['5%-95%', '10%-90%', '25%-75%', '中位数 (P50)'],
      bottom: 0,
    },
    xAxis: {
      type: 'category',
      data: xData,
      name: '交易日',
      boundaryGap: false,
      axisLabel: {
        formatter: (v: string) => {
          const day = parseInt(v);
          if (day === 0) return '起始';
          if (day % 63 === 0) return `${Math.round(day / 21)}月`;
          return '';
        },
        interval: 0,
      },
    },
    yAxis: {
      type: 'value',
      name: '资产价值 (¥)',
      axisLabel: {
        formatter: (v: number) => {
          if (v >= 10000) return `${(v / 10000).toFixed(1)}万`;
          return v.toLocaleString();
        },
      },
    },
    grid: { left: 80, right: 30, top: 40, bottom: 70 },
    series,
  };
}
