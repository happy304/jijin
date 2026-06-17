import { useState, useEffect, useCallback, useMemo } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import {
  Typography,
  Card,
  Spin,
  Alert,
  Button,
  Space,
  Progress,
  Table,
  Tag,
  Row,
  Col,
  Empty,
  Divider,
  Descriptions,
} from 'antd';
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
  ClockCircleOutlined,
  LineChartOutlined,
  ExperimentOutlined,
  ReloadOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import type { ColumnsType } from 'antd/es/table';
import { ResultInterpretationNotice, SampleScopeCard } from '@/components/DataTrustNotice';
import { DetailSection } from '@/components/DetailSection';
import { PageHero } from '@/components/PageHero';
import { StatCard } from '@/components/StatCard';
import { useFeatureProfile } from '@/api/settings';
import {
  useBacktestStatus,
  useBacktestEquity,
  useBacktestTrades,
  useBacktestAttribution,
  useAIAttributionReport,
  useBacktestRolling,
  useBacktestWalkForward,
  createBacktestProgressWs,
  useInvalidateBacktest,
  type BacktestProgressMessage,
  type TradeRecord,
  type BacktestMetrics,
  type BacktestNavDataStaleWarning,
  type BacktestNavQualityWarning,
  type BacktestQuality,
  type RollingMetricsResponse,
  type WalkForwardWindow,
  type AIAttributionReportResponse,
} from '@/api/backtests';

const { Text, Paragraph } = Typography;

const POSITIVE_COLOR = '#cf1322';
const NEGATIVE_COLOR = '#3f8600';

const MONTH_LABELS = [
  { key: '01', label: '1月' },
  { key: '02', label: '2月' },
  { key: '03', label: '3月' },
  { key: '04', label: '4月' },
  { key: '05', label: '5月' },
  { key: '06', label: '6月' },
  { key: '07', label: '7月' },
  { key: '08', label: '8月' },
  { key: '09', label: '9月' },
  { key: '10', label: '10月' },
  { key: '11', label: '11月' },
  { key: '12', label: '12月' },
];

type MonthlyReturnRow = {
  year: string;
  [month: string]: string | number | null;
};

type KeyValueRow = {
  key: string;
  label: string;
  value: number;
};

// ---------------------------------------------------------------------------
// Main Page Component
// ---------------------------------------------------------------------------

export function BacktestDetailPage() {
  const { runId: runIdParam } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const runId = runIdParam ? parseInt(runIdParam, 10) : undefined;

  const { data: backtest, isLoading, isError, error } = useBacktestStatus(runId);
  const { data: featureProfile } = useFeatureProfile();
  const aiAttributionEnabled = featureProfile?.feature_ai === true;

  if (isLoading) {
    return (
      <div className="page-shell">
          <Spin size="large">
          <div style={{ minHeight: 160 }} />
        </Spin>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="page-shell">
        <Alert
          type="error"
          message="加载失败"
          description={error instanceof Error ? error.message : '获取回测信息时发生错误'}
          showIcon
          action={
            <Button size="small" icon={<ArrowLeftOutlined />} onClick={() => navigate('/backtests')}>
              返回列表
            </Button>
          }
        />
      </div>
    );
  }

  if (!backtest || !runId) {
    return (
      <div className="page-shell">
        <Empty description="回测任务不存在">
          <Space wrap>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/backtests')}>
              返回列表
            </Button>
            <Link to="/strategies">
              <Button icon={<ExperimentOutlined />}>去策略管理</Button>
            </Link>
          </Space>
        </Empty>
      </div>
    );
  }

  return (
    <div className="detail-shell">
      <PageHero
        variant="detail"
        eyebrow={<><LineChartOutlined /> Backtest Detail</>}
        title={`回测 #${runId}`}
        meta={
          <>
            <span className="detail-pill"><StatusTag status={backtest.status} /></span>
            <span className="detail-pill">起始 {backtest.start_date}</span>
            <span className="detail-pill">结束 {backtest.end_date}</span>
            <span className="detail-pill">初始资金 {Number(backtest.initial_capital || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2 })}</span>
          </>
        }
        description="这里集中查看回测可信度门禁、收益曲线、交易历史、归因分析、滚动指标和 Walk-forward 结果。若数据质量有提醒，请先复核样本范围和净值口径，再解读绩效结论。"
        actions={
          <>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/backtests')}>
              返回列表
            </Button>
            <Link to="/strategies">
              <Button icon={<ExperimentOutlined />}>策略管理</Button>
            </Link>
            <Link to="/funds">
              <Button icon={<LineChartOutlined />}>基金检索</Button>
            </Link>
            <Button icon={<ReloadOutlined />} onClick={() => navigate(`/backtests/${runId}`)}>
              刷新查看
            </Button>
          </>
        }
        stats={
          <>
            <StatCard label="交易日" value={backtest.metrics?.trading_days ?? 'N/A'} note="样本覆盖的实际交易日数量" />
            <StatCard label="交易次数" value={backtest.metrics?.total_trades ?? 'N/A'} note="用于判断策略是否过度交易" />
            <StatCard label="可信度" value={backtest.quality?.decision_grade || 'research'} note="越接近 decision_support 越适合审计和解读" />
            <StatCard label="AI 归因" value={aiAttributionEnabled ? '启用' : '关闭'} note="用于生成归因说明，不影响回测计算" />
          </>
        }
      />

      <DetailSection title="回测概览" description="先看样本范围、可信度和数据提示">
        <ResultInterpretationNotice
          navDataStale={backtest.nav_data_stale}
          navQualityWarning={backtest.nav_quality_warning}
          tradingDays={backtest.metrics?.trading_days}
          totalTrades={backtest.metrics?.total_trades}
          style={{ marginBottom: 16 }}
        />

        <SampleScopeCard
          startDate={backtest.start_date}
          endDate={backtest.end_date}
          tradingDays={backtest.metrics?.trading_days}
          totalTrades={backtest.metrics?.total_trades}
          style={{ marginBottom: 16 }}
        />

        <NavDataWarnings
          navDataStale={backtest.nav_data_stale}
          navQualityWarning={backtest.nav_quality_warning}
        />
      </DetailSection>

      {(backtest.status === 'pending' || backtest.status === 'running') && (
        <DetailSection title="运行进度" description="WebSocket / 轮询同步更新">
          <BacktestProgress
            runId={runId}
            initialProgress={backtest.progress}
            progressMessage={backtest.progress_message}
            status={backtest.status}
            errorMessage={backtest.error_msg}
          />
        </DetailSection>
      )}

      {backtest.status === 'failed' && (
        <Card className="soft-card" style={{ marginBottom: 16 }}>
          <Alert
            type="error"
            message="回测失败"
            description={backtest.error_msg || '未知错误'}
            showIcon
            icon={<CloseCircleOutlined />}
          />
        </Card>
      )}

      {backtest.status === 'done' && (
        <>
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 16 }}
            message="回测结果用于历史样本验证"
            description="请结合样本区间、交易次数、最大回撤、数据质量提示和指标口径解读收益率、Sharpe 等指标。历史表现不代表未来收益，回测结论不构成投资建议或交易指令。"
          />

          <BacktestQualityGate quality={backtest.quality || backtest.metrics?.quality as BacktestQuality | undefined} />

          <DetailSection title="关键指标" description="收益、风险和稳定性概览">
            {backtest.metrics && <PerformanceMetrics metrics={backtest.metrics} />}
          </DetailSection>

          <DetailSection title="权益曲线与回撤" description="观察策略收益路径与风险暴露">
            <EquityChart runId={runId} />
          </DetailSection>

          <DetailSection title="交易历史" description="查看逐笔交易与执行结果">
            <TradeHistory runId={runId} />
          </DetailSection>

          <DetailSection title="归因分析" description="收益驱动与因子解释">
            <AttributionSection runId={runId} />
          </DetailSection>

          <DetailSection title="滚动指标" description="观察策略稳定性变化">
            <RollingMetricsSection runId={runId} />
          </DetailSection>

          <DetailSection title="Walk-forward 验证" description="检查样本外表现">
            <WalkForwardSection runId={runId} />
          </DetailSection>

          <DetailSection title="AI 归因报告" description="自然语言辅助解释结果">
            {aiAttributionEnabled ? (
              <AIAttributionReport runId={runId} />
            ) : (
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 16 }}
                message="AI 归因报告默认关闭"
                description="AI 归因属于高级辅助能力，可能产生调用成本和解释偏差；如需使用，可在环境变量中开启 AI 功能后重启服务。"
              />
            )}
          </DetailSection>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared formatters
// ---------------------------------------------------------------------------

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function lastFinite(values?: Array<number | null | undefined>) {
  if (!values) return undefined;
  for (let index = values.length - 1; index >= 0; index -= 1) {
    const value = values[index];
    if (isFiniteNumber(value)) return value;
  }
  return undefined;
}

function formatNumber(value: unknown, precision = 2, suffix = '') {
  if (!isFiniteNumber(value)) return '-';
  return `${value.toFixed(precision)}${suffix}`;
}

function formatPercent(value: unknown, precision = 2) {
  if (!isFiniteNumber(value)) return '-';
  return `${(value * 100).toFixed(precision)}%`;
}

function formatSignedPercent(value: unknown, precision = 2) {
  if (!isFiniteNumber(value)) return '-';
  const sign = value > 0 ? '+' : '';
  return `${sign}${(value * 100).toFixed(precision)}%`;
}

function formatCompactMoney(value: unknown) {
  if (!isFiniteNumber(value)) return '-';
  return value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function metricColor(value: unknown) {
  if (!isFiniteNumber(value)) return undefined;
  if (value > 0) return POSITIVE_COLOR;
  if (value < 0) return NEGATIVE_COLOR;
  return undefined;
}

function percentSeries(values: number[]) {
  return values.map((value) => (isFiniteNumber(value) ? Number((value * 100).toFixed(4)) : null));
}

function numberSeries(values: number[]) {
  return values.map((value) => (isFiniteNumber(value) ? Number(value.toFixed(4)) : null));
}

function ReturnValue({ value, precision = 2 }: { value: unknown; precision?: number }) {
  if (!isFiniteNumber(value)) return <Text type="secondary">-</Text>;
  return (
    <Text style={{ color: metricColor(value) }}>
      {formatSignedPercent(value, precision)}
    </Text>
  );
}

// ---------------------------------------------------------------------------
// Status Tag
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
  navDataStale?: BacktestNavDataStaleWarning | null;
  navQualityWarning?: BacktestNavQualityWarning | null;
}) {
  if (!navDataStale && !navQualityWarning) return null;

  const staleMessage =
    navDataStale?.message || '底层 NAV 复权口径已有更新，当前回测结果可能已过期，建议重新运行。';
  const qualityMessage =
    navQualityWarning?.message || '部分 NAV 数据存在口径混用或质量提示，请谨慎解读回测指标。';
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
// WebSocket Progress Component
// ---------------------------------------------------------------------------

function BacktestProgress({
  runId,
  initialProgress,
  progressMessage,
  status,
  errorMessage,
}: {
  runId: number;
  initialProgress: number;
  progressMessage?: string | null;
  status: string;
  errorMessage?: string | null;
}) {
  const [progress, setProgress] = useState(initialProgress);
  const [message, setMessage] = useState('正在连接...');
  const [wsConnected, setWsConnected] = useState(false);
  const invalidateBacktest = useInvalidateBacktest();

  useEffect(() => {
    setProgress(initialProgress);
  }, [initialProgress]);

  useEffect(() => {
    if (status === 'failed') {
      setMessage(errorMessage || '回测失败');
    } else if (status === 'done') {
      setMessage('回测完成');
      setProgress(100);
    } else if (progressMessage) {
      setMessage(progressMessage);
    }
  }, [status, errorMessage, progressMessage]);

  const handleMessage = useCallback(
    (msg: BacktestProgressMessage) => {
      setProgress(msg.progress);
      setMessage(msg.message);

      if (msg.status === 'done' || msg.type === 'complete') {
        setProgress(100);
        invalidateBacktest(runId);
      }

      if (msg.status === 'failed' || msg.type === 'error') {
        invalidateBacktest(runId);
      }
    },
    [runId, invalidateBacktest],
  );

  useEffect(() => {
    const cleanup = createBacktestProgressWs(
      runId,
      handleMessage,
      () => {
        setWsConnected(false);
        setMessage('连接断开，使用轮询模式...');
      },
      () => {
        setWsConnected(false);
      },
      () => {
        setWsConnected(true);
        setMessage('已连接，等待进度更新...');
      },
    );
    return () => {
      cleanup();
    };
  }, [runId, handleMessage]);

  return (
    <Card className="soft-card" style={{ marginBottom: 16 }}>
      <div style={{ textAlign: 'center', padding: '24px 0' }}>
        <Progress
          type="circle"
          percent={Math.round(progress * 100) / 100}
          status="active"
          size={120}
        />
        <div style={{ marginTop: 16 }}>
          <Text>{message}</Text>
        </div>
        <div style={{ marginTop: 8 }}>
          <Text type="secondary">
            {wsConnected ? 'WebSocket 实时更新' : '轮询模式（每 5 秒刷新）'}
          </Text>
        </div>
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Performance Metrics Cards
// ---------------------------------------------------------------------------

function BacktestQualityGate({ quality }: { quality?: BacktestQuality | null }) {
  if (!quality) {
    return (
      <Card className="soft-card" title="回测可信度门禁" style={{ marginBottom: 16 }}>
        <Alert
          type="warning"
          showIcon
          message="缺少回测质量标签"
          description="当前结果未提供完整的 PIT、现金到账、分批费用与数据质量假设，默认只能按研究近似解读。"
        />
      </Card>
    );
  }

  const decisionSupport = quality.decision_grade === 'decision_support';
  const boolTag = (ok?: boolean) => <Tag color={ok ? 'green' : 'orange'}>{ok ? '已建模' : '未确认'}</Tag>;
  const pitColor = quality.pit_data_quality === 'strict' ? 'green' : quality.pit_data_quality === 'fallback' ? 'orange' : 'red';
  const survivorshipColor = quality.survivorship_bias_control === 'full' ? 'green' : quality.survivorship_bias_control === 'partial' ? 'orange' : 'red';

  return (
    <Card className="soft-card" title="回测可信度门禁" style={{ marginBottom: 16 }}>
      <Alert
        type={decisionSupport ? 'success' : 'warning'}
        showIcon
        message={decisionSupport ? '决策支持级假设已满足' : '研究近似：不应标记为决策级'}
        description="该标签先于收益曲线展示，用于审计回测是否考虑了真实资金现金流、PIT 元数据、净值可见性与幸存者偏差风险。"
        style={{ marginBottom: 12 }}
      />
      <Space wrap>
        <Tag color={quality.lookahead_guard ? 'green' : 'red'}>防未来函数 {quality.lookahead_guard ? '通过' : '未确认'}</Tag>
        <span>到账延迟 {boolTag(quality.cash_arrival_delay_modelled)}</span>
        <span>分批费用 {boolTag(quality.lot_level_fee_modelled)}</span>
        <span>净值公布滞后 {boolTag(quality.nav_publication_lag_modelled)}</span>
        <Tag color={pitColor}>PIT: {quality.pit_data_quality || 'missing'}</Tag>
        <Tag color={survivorshipColor}>幸存者偏差: {quality.survivorship_bias_control || 'none'}</Tag>
        <Tag color={quality.vectorized_simplification ? 'red' : 'green'}>
          {quality.vectorized_simplification ? '向量化简化' : '事件驱动'}
        </Tag>
      </Space>
      {quality.warnings && quality.warnings.length > 0 && (
        <Alert
          type="info"
          showIcon
          style={{ marginTop: 12 }}
          message="可信度提示"
          description={quality.warnings.slice(0, 5).join('；')}
        />
      )}
    </Card>
  );
}

function PerformanceMetrics({ metrics }: { metrics: BacktestMetrics }) {
  const items = [
    {
      key: 'total_return',
      title: '总收益率',
      value: metrics.total_return != null ? metrics.total_return * 100 : undefined,
      suffix: '%',
      precision: 2,
      color: (metrics.total_return ?? 0) >= 0 ? POSITIVE_COLOR : NEGATIVE_COLOR,
    },
    {
      key: 'annualized_return',
      title: '年化收益率',
      value: metrics.annualized_return != null ? metrics.annualized_return * 100 : undefined,
      suffix: '%',
      precision: 2,
      color: (metrics.annualized_return ?? 0) >= 0 ? POSITIVE_COLOR : NEGATIVE_COLOR,
    },
    {
      key: 'sharpe',
      title: 'Sharpe 比率',
      value: metrics.sharpe,
      precision: 3,
    },
    {
      key: 'max_drawdown',
      title: '最大回撤',
      value: metrics.max_drawdown != null ? metrics.max_drawdown * 100 : undefined,
      suffix: '%',
      precision: 2,
      color: POSITIVE_COLOR,
    },
    {
      key: 'max_drawdown_recovery_days',
      title: '回撤恢复耗时',
      value: metrics.max_drawdown_recovery_days ?? undefined,
      suffix: '天',
      precision: 0,
      description: metrics.max_drawdown_recovery_date
        ? `最大回撤恢复日：${metrics.max_drawdown_recovery_date}`
        : '尚未恢复或无显著回撤。',
    },
    {
      key: 'volatility',
      title: '年化波动率',
      value: metrics.volatility != null ? metrics.volatility * 100 : undefined,
      suffix: '%',
      precision: 2,
    },
    {
      key: 'sortino',
      title: 'Sortino 比率',
      value: metrics.sortino,
      precision: 3,
    },
    {
      key: 'calmar',
      title: 'Calmar 比率',
      value: metrics.calmar,
      precision: 3,
    },
    {
      key: 'win_rate',
      title: '日频胜率',
      value: metrics.win_rate != null ? metrics.win_rate * 100 : undefined,
      suffix: '%',
      precision: 1,
      description: '组合权益曲线日收益为正的交易日占比，不是交易级胜率。',
    },
    {
      key: 'profit_factor',
      title: '日频盈亏比',
      value: metrics.profit_factor,
      precision: 3,
      description: '日盈利收益总和 / 日亏损收益总和绝对值。',
    },
    {
      key: 'cashflow_win_rate_estimate',
      title: '现金流估算胜率',
      value: (metrics.cashflow_win_rate_estimate ?? metrics.trade_win_rate) != null ? (metrics.cashflow_win_rate_estimate ?? metrics.trade_win_rate)! * 100 : undefined,
      suffix: '%',
      precision: 1,
      description: metrics.trade_metrics_note || '按基金代码聚合现金流估算，非严格逐笔配对；该指标已降级，不作为核心专业交易胜率。',
      secondary: true,
    },
    {
      key: 'cashflow_profit_factor_estimate',
      title: '现金流估算盈亏比',
      value: metrics.cashflow_profit_factor_estimate ?? metrics.trade_profit_factor,
      precision: 3,
      description: metrics.trade_metrics_note || '基于估算现金流盈亏计算，不能等同于严格 lot-level 已实现交易盈亏比。',
      secondary: true,
    },
    {
      key: 'information_ratio',
      title: '信息比率',
      value: metrics.information_ratio,
      precision: 3,
      description: '相对基准的风险调整表现。',
    },
    {
      key: 'tracking_error',
      title: '跟踪误差',
      value: metrics.tracking_error != null ? metrics.tracking_error * 100 : undefined,
      suffix: '%',
      precision: 2,
      description: '相对基准的波动。',
    },
  ];

  return (
    <Card className="soft-card">
      <div className="detail-stat-grid detail-stat-grid-four">
        {items.slice(0, 4).map((item) => (
          <StatCard
            key={item.key}
            label={item.title}
            value={item.value == null ? '-' : `${Number(item.value).toFixed(item.precision ?? 2)}${item.suffix || ''}`}
            color={item.color}
            note={item.description || '关键回测指标'}
          />
        ))}
      </div>
      <Divider />
      <Row gutter={[12, 12]}>
        {items.slice(4).map((item) => (
          <Col xs={24} sm={12} lg={8} key={item.key}>
            <StatCard
              size="small"
              label={item.title}
              value={item.value == null ? '-' : `${Number(item.value).toFixed(item.precision ?? 2)}${item.suffix || ''}`}
              color={item.color}
              valueStyle={{ fontSize: item.secondary ? 18 : 18 }}
              note={item.description || '辅助指标'}
            />
          </Col>
        ))}
      </Row>
    </Card>
  );
}

function EquityChart({ runId }: { runId: number }) {
  const { data, isLoading, isError, error } = useBacktestEquity(runId);

  const option = useMemo<EChartsOption>(() => {
    const records = data?.records || [];
    if (records.length === 0) return {};

    const dates = records.map((item) => item.trade_date);
    const equity = records.map((item) => item.equity);
    const benchmark = records.some((item) => item.benchmark_value != null)
      ? records.map((item) => item.benchmark_value ?? null)
      : null;
    const series: EChartsOption['series'] = [
      {
        name: '组合权益',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: equity,
      },
      ...(benchmark
        ? [{
            name: '基准权益',
            type: 'line' as const,
            smooth: true,
            showSymbol: false,
            data: benchmark,
          }]
        : []),
    ];

    return {
      color: ['#176bff', '#0fb7a5'],
      tooltip: { trigger: 'axis' },
      legend: { data: benchmark ? ['组合权益', '基准权益'] : ['组合权益'] },
      grid: { left: 48, right: 24, top: 48, bottom: 48 },
      xAxis: { type: 'category', data: dates },
      yAxis: { type: 'value', name: '权益' },
      series,
    };
  }, [data]);

  if (isLoading) {
    return (
      <Card className="soft-card" style={{ marginBottom: 16 }}>
        <Spin>
          <div style={{ minHeight: 120 }} />
        </Spin>
      </Card>
    );
  }

  if (isError) {
    return (
      <Alert
        type="error"
        message="权益曲线加载失败"
        description={error instanceof Error ? error.message : '请稍后重试'}
        showIcon
        style={{ marginBottom: 16 }}
      />
    );
  }

  if (!data || data.records.length === 0) {
    return (
      <Card className="soft-card" style={{ marginBottom: 16 }}>
        <Empty description="暂无权益数据" />
      </Card>
    );
  }

  return (
    <Card className="soft-card" style={{ marginBottom: 16 }}>
      <ReactECharts option={option} style={{ height: 420 }} />
    </Card>
  );
}

function TradeHistory({ runId }: { runId: number }) {
  const { data, isLoading } = useBacktestTrades(runId);

  const columns: ColumnsType<TradeRecord> = [
    { title: '下单日', dataIndex: 'order_date', key: 'order_date', width: 110 },
    { title: '确认日', dataIndex: 'confirm_date', key: 'confirm_date', width: 110, render: (v: string | null) => v || '-' },
    { title: '基金代码', dataIndex: 'fund_code', key: 'fund_code', width: 100 },
    {
      title: '方向',
      dataIndex: 'direction',
      key: 'direction',
      width: 90,
      render: (v: string) => <Tag color={v === 'subscribe' ? 'green' : 'red'}>{v === 'subscribe' ? '申购' : '赎回'}</Tag>,
    },
    { title: '份额', dataIndex: 'shares', key: 'shares', width: 100, render: (v: number | null) => v == null ? '-' : v.toFixed(2) },
    { title: '净值', dataIndex: 'nav', key: 'nav', width: 100, render: (v: number | null) => v == null ? '-' : v.toFixed(4) },
    { title: '金额', dataIndex: 'amount', key: 'amount', width: 120, render: (v: number) => formatCompactMoney(v) },
    { title: '费用', dataIndex: 'fee', key: 'fee', width: 100, render: (v: number) => formatCompactMoney(v) },
  ];

  return (
    <Card className="soft-card" style={{ marginBottom: 16 }}>
      <Table<TradeRecord>
        columns={columns}
        dataSource={data?.items || []}
        rowKey={(row) => row.trade_id}
        pagination={data ? { current: data.page, pageSize: data.page_size, total: data.total } : false}
        loading={isLoading}
        size="small"
        locale={{ emptyText: <Empty description="暂无交易记录" /> }}
        scroll={{ x: 900 }}
      />
    </Card>
  );
}

function AttributionSection({ runId }: { runId: number }) {
  const { data, isLoading, isError, error } = useBacktestAttribution(runId);

  const option = useMemo<EChartsOption>(() => {
    const brinson = data?.brinson;
    if (!brinson) return {};
    const rows = [
      { name: '配置效应', value: brinson.allocation_effect },
      { name: '选择效应', value: brinson.selection_effect },
      { name: '交互效应', value: brinson.interaction_effect },
      { name: '总超额', value: brinson.total_excess },
    ];
    return {
      color: ['#176bff'],
      tooltip: {
        trigger: 'axis',
        valueFormatter: (value) => `${Number(value).toFixed(2)}%`,
      },
      grid: { left: 48, right: 18, top: 28, bottom: 42 },
      xAxis: { type: 'category', data: rows.map((row) => row.name) },
      yAxis: { type: 'value', name: '贡献' },
      series: [{ name: '贡献', type: 'bar', data: rows.map((row) => Number((row.value * 100).toFixed(4))) }],
    };
  }, [data]);

  if (isLoading) {
    return <Card className="soft-card" style={{ marginBottom: 16 }}><Spin><div style={{ minHeight: 120 }} /></Spin></Card>;
  }

  if (isError) {
    return (
      <Alert
        type="error"
        showIcon
        style={{ marginBottom: 16 }}
        message="归因分析加载失败"
        description={error instanceof Error ? error.message : '请稍后重试'}
      />
    );
  }

  if (!data || (!data.fama_french && !data.brinson)) {
    return <Card className="soft-card" style={{ marginBottom: 16 }}><Empty description="暂无归因分析" /></Card>;
  }

  const fama = data.fama_french;
  const brinson = data.brinson;
  const brinsonRows: KeyValueRow[] = brinson ? [
    { key: 'allocation', label: '配置效应', value: brinson.allocation_effect },
    { key: 'selection', label: '选择效应', value: brinson.selection_effect },
    { key: 'interaction', label: '交互效应', value: brinson.interaction_effect },
    { key: 'total_excess', label: '总超额', value: brinson.total_excess },
  ] : [];

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card className="soft-card">
        <div className="detail-stat-grid detail-stat-grid-four">
          <StatCard label="Alpha" value={formatSignedPercent(fama?.alpha, 2)} color={metricColor(fama?.alpha)} note="Fama-French 截距项" />
          <StatCard label="市场 Beta" value={formatNumber(fama?.beta_mkt, 3)} note="对市场因子的暴露" />
          <StatCard label="R²" value={formatPercent(fama?.r_squared, 1)} note="因子模型解释度" />
          <StatCard label="总超额" value={formatSignedPercent(brinson?.total_excess, 2)} color={metricColor(brinson?.total_excess)} note="Brinson 归因汇总" />
        </div>
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <Card className="soft-card" title="Fama-French 因子暴露" style={{ height: '100%' }}>
            {fama ? (
              <Descriptions column={1} size="small">
                <Descriptions.Item label="Alpha">{formatSignedPercent(fama.alpha, 2)}</Descriptions.Item>
                <Descriptions.Item label="市场因子 Beta">{formatNumber(fama.beta_mkt, 3)}</Descriptions.Item>
                <Descriptions.Item label="规模因子 SMB">{formatNumber(fama.beta_smb, 3)}</Descriptions.Item>
                <Descriptions.Item label="价值因子 HML">{formatNumber(fama.beta_hml, 3)}</Descriptions.Item>
                <Descriptions.Item label="盈利因子 RMW">{formatNumber(fama.beta_rmw, 3)}</Descriptions.Item>
                <Descriptions.Item label="投资因子 CMA">{formatNumber(fama.beta_cma, 3)}</Descriptions.Item>
                <Descriptions.Item label="R²">{formatPercent(fama.r_squared, 1)}</Descriptions.Item>
              </Descriptions>
            ) : (
              <Empty description="暂无 Fama-French 归因" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card className="soft-card" title="Brinson 贡献拆解" style={{ height: '100%' }}>
            {brinson ? (
              <>
                <ReactECharts option={option} style={{ height: 260 }} />
                <Table<KeyValueRow>
                  rowKey="key"
                  size="small"
                  pagination={false}
                  dataSource={brinsonRows}
                  columns={[
                    { title: '归因项', dataIndex: 'label', key: 'label' },
                    {
                      title: '贡献',
                      dataIndex: 'value',
                      key: 'value',
                      align: 'right',
                      render: (value: number) => <ReturnValue value={value} />,
                    },
                  ]}
                />
              </>
            ) : (
              <Empty description="暂无 Brinson 归因" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>
        </Col>
      </Row>
    </Space>
  );
}

function RollingMetricsSection({ runId }: { runId: number }) {
  const { data, isLoading, isError, error } = useBacktestRolling(runId);

  const rollingOption = useMemo<EChartsOption>(() => buildRollingChartOption(data), [data]);
  const monthlyRows = useMemo(() => buildMonthlyRows(data?.monthly_returns), [data]);
  const yearlyRows = useMemo(() => buildYearlyRows(data?.yearly_returns), [data]);

  if (isLoading) {
    return <Card className="soft-card" style={{ marginBottom: 16 }}><Spin><div style={{ minHeight: 120 }} /></Spin></Card>;
  }

  if (isError) {
    return (
      <Alert
        type="error"
        showIcon
        style={{ marginBottom: 16 }}
        message="滚动指标加载失败"
        description={error instanceof Error ? error.message : '请稍后重试'}
      />
    );
  }

  if (!data || data.dates.length === 0) {
    return <Card className="soft-card" style={{ marginBottom: 16 }}><Empty description="暂无滚动指标" /></Card>;
  }

  const latestReturn = lastFinite(data.rolling_return);
  const latestSharpe = lastFinite(data.rolling_sharpe);
  const latestDrawdown = lastFinite(data.rolling_drawdown);
  const latestVolatility = lastFinite(data.rolling_volatility);

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card className="soft-card">
        <div className="detail-stat-grid detail-stat-grid-four">
          <StatCard label="最近滚动收益" value={formatSignedPercent(latestReturn)} color={metricColor(latestReturn)} note="最后一个滚动窗口收益" />
          <StatCard label="最近滚动 Sharpe" value={formatNumber(latestSharpe, 3)} note="最后一个滚动窗口风险调整表现" />
          <StatCard label="最近滚动回撤" value={formatPercent(latestDrawdown)} color={NEGATIVE_COLOR} note="窗口内最大回撤" />
          <StatCard label="最近滚动波动" value={formatPercent(latestVolatility)} note="窗口内年化波动率" />
        </div>
      </Card>

      <Card className="soft-card" title="滚动收益、Sharpe、回撤与波动">
        <ReactECharts option={rollingOption} style={{ height: 420 }} />
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={8}>
          <Card className="soft-card" title="年度收益" style={{ height: '100%' }}>
            <Table
              rowKey="year"
              size="small"
              pagination={false}
              dataSource={yearlyRows}
              columns={[
                { title: '年份', dataIndex: 'year', key: 'year' },
                {
                  title: '收益率',
                  dataIndex: 'return',
                  key: 'return',
                  align: 'right' as const,
                  render: (value: number) => <ReturnValue value={value} />,
                },
              ]}
              locale={{ emptyText: <Empty description="暂无年度收益" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
            />
          </Card>
        </Col>
        <Col xs={24} lg={16}>
          <Card className="soft-card" title="月度收益矩阵" style={{ height: '100%' }}>
            <Table<MonthlyReturnRow>
              rowKey="year"
              size="small"
              pagination={false}
              dataSource={monthlyRows}
              scroll={{ x: 900 }}
              columns={[
                { title: '年份', dataIndex: 'year', key: 'year', fixed: 'left', width: 80 },
                ...MONTH_LABELS.map((month) => ({
                  title: month.label,
                  dataIndex: month.key,
                  key: month.key,
                  align: 'right' as const,
                  width: 70,
                  render: (value: string | number | null) => <ReturnValue value={value} precision={1} />,
                })),
              ]}
              locale={{ emptyText: <Empty description="暂无月度收益" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
            />
          </Card>
        </Col>
      </Row>
    </Space>
  );
}

function buildRollingChartOption(data?: RollingMetricsResponse): EChartsOption {
  if (!data || data.dates.length === 0) return {};
  return {
    color: ['#176bff', '#d9a441', '#d84a4a', '#0fb7a5'],
    tooltip: { trigger: 'axis' },
    legend: { data: ['滚动收益', '滚动 Sharpe', '滚动回撤', '滚动波动'] },
    grid: { left: 52, right: 56, top: 56, bottom: 54 },
    xAxis: { type: 'category', data: data.dates },
    yAxis: [
      { type: 'value', name: '百分比', axisLabel: { formatter: '{value}%' } },
      { type: 'value', name: 'Sharpe' },
    ],
    dataZoom: data.dates.length > 60 ? [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 12 }] : undefined,
    series: [
      { name: '滚动收益', type: 'line', smooth: true, showSymbol: false, data: percentSeries(data.rolling_return) },
      { name: '滚动 Sharpe', type: 'line', smooth: true, showSymbol: false, yAxisIndex: 1, data: numberSeries(data.rolling_sharpe) },
      { name: '滚动回撤', type: 'line', smooth: true, showSymbol: false, data: percentSeries(data.rolling_drawdown) },
      { name: '滚动波动', type: 'line', smooth: true, showSymbol: false, data: percentSeries(data.rolling_volatility) },
    ],
  };
}

function buildYearlyRows(yearlyReturns?: Record<string, number>) {
  return Object.entries(yearlyReturns || {})
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([year, value]) => ({ year, return: value }));
}

function buildMonthlyRows(monthlyReturns?: Record<string, number>): MonthlyReturnRow[] {
  const rows = new Map<string, MonthlyReturnRow>();
  Object.entries(monthlyReturns || {})
    .sort(([a], [b]) => a.localeCompare(b))
    .forEach(([period, value]) => {
      const [year, rawMonth] = period.split('-');
      if (!year || !rawMonth) return;
      const month = rawMonth.padStart(2, '0');
      const row = rows.get(year) || { year };
      row[month] = value;
      rows.set(year, row);
    });
  return Array.from(rows.values());
}

function WalkForwardSection({ runId }: { runId: number }) {
  const { data, isLoading, isError, error } = useBacktestWalkForward(runId);

  const option = useMemo<EChartsOption>(() => {
    const windows = data?.windows || [];
    if (windows.length === 0) return {};
    const labels = windows.map((item) => `W${item.window_id}`);
    return {
      color: ['#176bff', '#0fb7a5', '#d9a441'],
      tooltip: { trigger: 'axis' },
      legend: { data: ['样本内 Sharpe', '样本外 Sharpe', '样本外收益'] },
      grid: { left: 52, right: 56, top: 56, bottom: 48 },
      xAxis: { type: 'category', data: labels },
      yAxis: [
        { type: 'value', name: 'Sharpe' },
        { type: 'value', name: '收益率', axisLabel: { formatter: '{value}%' } },
      ],
      series: [
        { name: '样本内 Sharpe', type: 'line', smooth: true, data: windows.map((item) => item.is_sharpe) },
        { name: '样本外 Sharpe', type: 'line', smooth: true, data: windows.map((item) => item.oos_sharpe) },
        { name: '样本外收益', type: 'bar', yAxisIndex: 1, data: windows.map((item) => Number((item.oos_return * 100).toFixed(4))) },
      ],
    };
  }, [data]);

  if (isLoading) {
    return <Card className="soft-card" style={{ marginBottom: 16 }}><Spin><div style={{ minHeight: 120 }} /></Spin></Card>;
  }

  if (isError) {
    return (
      <Alert
        type="error"
        showIcon
        style={{ marginBottom: 16 }}
        message="Walk-forward 结果加载失败"
        description={error instanceof Error ? error.message : '请稍后重试'}
      />
    );
  }

  if (!data) {
    return <Card className="soft-card" style={{ marginBottom: 16 }}><Empty description="暂无 Walk-forward 结果" /></Card>;
  }

  const columns: ColumnsType<WalkForwardWindow> = [
    { title: '窗口', dataIndex: 'window_id', key: 'window_id', width: 80, fixed: 'left' },
    { title: '训练区间', key: 'train', width: 210, render: (_, row) => `${row.train_start} → ${row.train_end}` },
    { title: '测试区间', key: 'test', width: 210, render: (_, row) => `${row.test_start} → ${row.test_end}` },
    { title: 'IS Sharpe', dataIndex: 'is_sharpe', key: 'is_sharpe', width: 110, align: 'right', render: (value: number) => formatNumber(value, 3) },
    { title: 'OOS Sharpe', dataIndex: 'oos_sharpe', key: 'oos_sharpe', width: 120, align: 'right', render: (value: number) => formatNumber(value, 3) },
    { title: 'IS 收益', dataIndex: 'is_return', key: 'is_return', width: 110, align: 'right', render: (value: number) => <ReturnValue value={value} /> },
    { title: 'OOS 收益', dataIndex: 'oos_return', key: 'oos_return', width: 120, align: 'right', render: (value: number) => <ReturnValue value={value} /> },
    { title: 'IS 最大回撤', dataIndex: 'is_max_drawdown', key: 'is_max_drawdown', width: 130, align: 'right', render: (value: number) => formatPercent(value) },
    { title: 'OOS 最大回撤', dataIndex: 'oos_max_drawdown', key: 'oos_max_drawdown', width: 140, align: 'right', render: (value: number) => formatPercent(value) },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {data.note && (
        <Alert
          type={data.windows.length > 0 ? 'info' : 'warning'}
          showIcon
          message="Walk-forward 说明"
          description={data.note}
        />
      )}

      <Card className="soft-card">
        <div className="detail-stat-grid detail-stat-grid-six">
          <StatCard label="WFE" value={formatNumber(data.wfe, 2)} note="样本外 / 样本内 Sharpe" />
          <StatCard label="平均 IS Sharpe" value={formatNumber(data.avg_is_sharpe, 3)} note="训练窗口平均表现" />
          <StatCard label="平均 OOS Sharpe" value={formatNumber(data.avg_oos_sharpe, 3)} note="测试窗口平均表现" />
          <StatCard label="OOS 胜率" value={formatPercent(data.oos_win_rate, 1)} note="样本外正收益窗口占比" />
          <StatCard label="OOS 总收益" value={formatSignedPercent(data.total_oos_return)} color={metricColor(data.total_oos_return)} note="按窗口复合后的样本外收益" />
          <StatCard label="稳健性" value={<Tag color={data.is_robust ? 'green' : 'orange'}>{data.is_robust ? '稳健' : '需谨慎'}</Tag>} note="WFE 与 OOS 胜率综合判断" />
        </div>
      </Card>

      {data.windows.length > 0 ? (
        <>
          <Card className="soft-card" title="窗口表现对比">
            <ReactECharts option={option} style={{ height: 380 }} />
          </Card>
          <Card className="soft-card" title="窗口明细">
            <Table<WalkForwardWindow>
              rowKey="window_id"
              size="small"
              columns={columns}
              dataSource={data.windows}
              pagination={data.windows.length > 8 ? { pageSize: 8, showSizeChanger: false } : false}
              scroll={{ x: 1250 }}
            />
          </Card>
        </>
      ) : (
        <Card className="soft-card">
          <Empty description="未生成有效 Walk-forward 窗口" />
        </Card>
      )}
    </Space>
  );
}

function AIAttributionReport({ runId }: { runId: number }) {
  const { data, mutate, isPending, isError, error } = useAIAttributionReport(runId);

  if (!data) {
    return (
      <Card className="soft-card" style={{ marginBottom: 16 }}>
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="按需生成 AI 归因报告"
            description="AI 报告会基于已计算好的归因和绩效指标生成自然语言解释，不会替代结构化数据和人工复核。"
          />
          {isError && (
            <Alert
              type="error"
              showIcon
              message="AI 归因报告生成失败"
              description={error instanceof Error ? error.message : '请稍后重试'}
            />
          )}
          <Button type="primary" icon={<ThunderboltOutlined />} loading={isPending} onClick={() => mutate()}>
            生成 AI 归因报告
          </Button>
        </Space>
      </Card>
    );
  }

  return <AIAttributionReportCard data={data} />;
}

function AIAttributionReportCard({ data }: { data: AIAttributionReportResponse }) {
  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card className="soft-card" title="AI 归因报告" extra={<Tag color="purple">AI 生成</Tag>}>
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert type="warning" showIcon message={data.ai_generated_label || 'AI 生成内容，仅供参考'} />
          <Paragraph style={{ whiteSpace: 'pre-line', marginBottom: 0 }}>
            {data.report_text || '报告为空，请稍后重新生成。'}
          </Paragraph>
          {data.data_link && (
            <Text type="secondary">
              原始数据接口：<Text code>{data.data_link}</Text>
            </Text>
          )}
        </Space>
      </Card>
      <AIInputSummary inputData={data.input_data} />
    </Space>
  );
}

function AIInputSummary({ inputData }: { inputData: Record<string, unknown> }) {
  const strategyName = typeof inputData.strategy_name === 'string' ? inputData.strategy_name : '未命名策略';
  const groups = [
    { key: 'return_metrics', title: '收益指标', data: asRecord(inputData.return_metrics) },
    { key: 'risk_metrics', title: '风险指标', data: asRecord(inputData.risk_metrics) },
    { key: 'risk_adjusted_metrics', title: '风险调整指标', data: asRecord(inputData.risk_adjusted_metrics) },
    { key: 'benchmark_metrics', title: '基准对比指标', data: asRecord(inputData.benchmark_metrics) },
    { key: 'fama_french', title: 'Fama-French 输入', data: asRecord(inputData.fama_french) },
    { key: 'brinson', title: 'Brinson 输入', data: asRecord(inputData.brinson) },
  ].filter((group) => group.data && Object.keys(group.data).length > 0);

  return (
    <Card className="soft-card" title="AI 报告使用的数据摘要" extra={<Tag color="blue">{strategyName}</Tag>}>
      {groups.length === 0 ? (
        <Empty description="暂无输入数据摘要" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Row gutter={[12, 12]}>
          {groups.map((group) => (
            <Col xs={24} lg={12} key={group.key}>
              <Card size="small" className="metric-list-card" title={group.title}>
                <Descriptions column={1} size="small">
                  {Object.entries(group.data || {}).slice(0, 8).map(([key, value]) => (
                    <Descriptions.Item label={key} key={key}>{formatUnknownMetric(value)}</Descriptions.Item>
                  ))}
                </Descriptions>
              </Card>
            </Col>
          ))}
        </Row>
      )}
    </Card>
  );
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function formatUnknownMetric(value: unknown) {
  if (isFiniteNumber(value)) return Math.abs(value) <= 1 ? formatPercent(value) : formatNumber(value, 3);
  if (value == null) return '-';
  if (typeof value === 'boolean') return value ? '是' : '否';
  if (typeof value === 'string') return value;
  if (Array.isArray(value)) return `${value.length} 项`;
  if (typeof value === 'object') return '结构化数据';
  return String(value);
}
