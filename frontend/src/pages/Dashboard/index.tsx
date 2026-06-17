import { Card, Table, Tag, Spin, Empty, Alert, Button, Space, Typography } from 'antd';
import {
  FundOutlined,
  LineChartOutlined,
  DatabaseOutlined,
  CalendarOutlined,
  SafetyCertificateOutlined,
  BulbOutlined,
  SearchOutlined,
  RocketOutlined,
  ArrowRightOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import dayjs from 'dayjs';
import apiClient from '@/api/client';
import { useFundNavQualityOverview } from '@/api/funds';

const { Text } = Typography;

const DATA_FRESH_WARN_DAYS = 5;
const DATA_FRESH_STALE_DAYS = 10;
const personalMode = import.meta.env.VITE_PERSONAL_MODE !== 'false';

const QUALITY_STATUS_LABELS: Record<string, string> = {
  good: '良好',
  warning: '需关注',
  poor: '较差',
};

const QUALITY_STATUS_COLORS: Record<string, string> = {
  good: 'green',
  warning: 'orange',
  poor: 'red',
};

function getQualitySummary(statusCounts: Record<string, number> | undefined) {
  const good = statusCounts?.good || 0;
  const warning = statusCounts?.warning || 0;
  const poor = statusCounts?.poor || 0;
  if (poor > 0) {
    return {
      type: 'error' as const,
      label: '存在较差数据',
      description: `发现 ${poor} 只基金数据质量较差，建议先到数据质量页复核 NAV 覆盖率、复权覆盖率、缺口和跳变。`,
    };
  }
  if (warning > 0) {
    return {
      type: 'warning' as const,
      label: '存在需关注数据',
      description: `发现 ${warning} 只基金存在数据质量提示，关键筛选、回测和组合检查前建议先复核。`,
    };
  }
  if (good > 0) {
    return {
      type: 'success' as const,
      label: '质量概览良好',
      description: '当前 NAV 质量概览未发现明显问题，仍建议在关键分析前确认数据日期与模型假设。',
    };
  }
  return {
    type: 'info' as const,
    label: '暂无质量概览',
    description: '暂无 NAV 质量概览数据，请先完成数据采集或进入基金检索页查看详情。',
  };
}

function getDataFreshness(navLatestDate: string | null) {
  if (!navLatestDate) {
    return {
      status: 'error' as const,
      label: '暂无净值数据',
      description: '请先完成数据采集，再进行筛选、回测或组合检查。',
      tagColor: 'red',
    };
  }

  const latest = dayjs(navLatestDate);
  const lagDays = Math.max(dayjs().startOf('day').diff(latest.startOf('day'), 'day'), 0);

  if (lagDays >= DATA_FRESH_STALE_DAYS) {
    return {
      status: 'error' as const,
      label: `数据滞后 ${lagDays} 天`,
      description: '当前结果可能依赖过期净值，建议先执行数据更新任务。',
      tagColor: 'red',
    };
  }

  if (lagDays >= DATA_FRESH_WARN_DAYS) {
    return {
      status: 'warning' as const,
      label: `数据可能滞后 ${lagDays} 天`,
      description: '建议在关键分析前确认净值、复权和基准数据是否已更新。',
      tagColor: 'orange',
    };
  }

  return {
    status: 'success' as const,
    label: lagDays === 0 ? '数据为今日更新' : `数据更新于 ${lagDays} 天前`,
    description: '可用于个人研究、基金筛选、回测验证和组合风险检查。',
    tagColor: 'green',
  };
}

interface RecentBacktest {
  run_id: number;
  strategy_name: string | null;
  status: string;
  total_return: number | null;
  sharpe: number | null;
  max_drawdown: number | null;
  finished_at: string | null;
}

interface DashboardStats {
  fund_count: number;
  strategy_count: number;
  backtest_count: number;
  nav_latest_date: string | null;
  nav_total_records: number;
  recent_backtests: RecentBacktest[];
}

async function fetchDashboard(): Promise<DashboardStats> {
  const { data } = await apiClient.get<DashboardStats>('/v1/dashboard');
  return data;
}

function useDashboard() {
  return useQuery({
    queryKey: ['dashboard'],
    queryFn: fetchDashboard,
    staleTime: 30000,
  });
}

function formatCompactNumber(value: number) {
  if (value >= 10000) return `${(value / 10000).toFixed(1)}万`;
  return value.toLocaleString('zh-CN');
}

function MetricCard({
  label,
  value,
  note,
  icon,
  color,
  onClick,
}: {
  label: string;
  value: string | number;
  note: string;
  icon: React.ReactNode;
  color: string;
  onClick?: () => void;
}) {
  return (
    <div
      className={`metric-card${onClick ? ' is-clickable' : ''}`}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={(event) => {
        if (onClick && (event.key === 'Enter' || event.key === ' ')) onClick();
      }}
      style={{
        '--metric-color': color,
        '--metric-bg': `${color}18`,
        '--metric-glow': `${color}22`,
      } as React.CSSProperties}
    >
      <div className="metric-card-icon">{icon}</div>
      <div className="metric-card-label">{label}</div>
      <div className="metric-card-value">{value}</div>
      <div className="metric-card-note">{note}</div>
    </div>
  );
}

function ActionCard({
  to,
  icon,
  title,
  description,
}: {
  to: string;
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <Link to={to} className="action-card">
      <div className="action-card-icon">{icon}</div>
      <div>
        <strong>{title}</strong>
        <span>{description}</span>
      </div>
    </Link>
  );
}

export function DashboardPage() {
  const { data, isLoading } = useDashboard();
  const { data: navQualityOverview, isLoading: qualityLoading } = useFundNavQualityOverview({ page: 1, page_size: 5 });
  const navigate = useNavigate();

  if (isLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" tip="正在整理研究工作台..." />
      </div>
    );
  }

  const stats = data ?? {
    fund_count: 0,
    strategy_count: 0,
    backtest_count: 0,
    nav_latest_date: null,
    nav_total_records: 0,
    recent_backtests: [],
  };
  const dataFreshness = getDataFreshness(stats.nav_latest_date);
  const qualitySummary = getQualitySummary(navQualityOverview?.status_counts);
  const qualitySamples = (navQualityOverview?.items || [])
    .filter((item) => item.status === 'warning' || item.status === 'poor')
    .slice(0, 3);

  return (
    <div className="page-shell">
      <section className="page-hero">
        <div className="page-hero-content">
          <div className="page-eyebrow">
            <ThunderboltOutlined /> Fund Quant Research
          </div>
          <h1>{personalMode ? '个人基金研究工作台' : '基金量化研究工作台'}</h1>
          <p>
            从数据质量开始，依次完成基金发现、检索筛选、回测验证和组合风险检查。
            本平台仅用于个人研究辅助，不构成投资建议或交易指令。
          </p>
          <div className="page-hero-actions">
            <Button type="primary" size="large" icon={<SearchOutlined />} onClick={() => navigate('/funds')}>
              开始检索基金
            </Button>
            <Button size="large" icon={<BulbOutlined />} onClick={() => navigate('/advisor')}>
              检查当前组合
            </Button>
            <Button size="large" icon={<LineChartOutlined />} onClick={() => navigate('/backtests')}>
              查看回测记录
            </Button>
          </div>
        </div>
      </section>

      <div className="metric-grid">
        <MetricCard
          label="基金总数"
          value={stats.fund_count.toLocaleString('zh-CN')}
          note="点击进入本地基金库和在线检索"
          icon={<FundOutlined />}
          color="#176bff"
          onClick={() => navigate('/funds')}
        />
        <MetricCard
          label="组合检查"
          value="研究辅助"
          note="结合持仓、风险和数据质量生成参考"
          icon={<BulbOutlined />}
          color="#1f9d68"
          onClick={() => navigate('/advisor')}
        />
        <MetricCard
          label="回测次数"
          value={stats.backtest_count.toLocaleString('zh-CN')}
          note="用于验证策略表现、回撤和交易成本"
          icon={<LineChartOutlined />}
          color="#7a4fe8"
          onClick={() => navigate('/backtests')}
        />
        <MetricCard
          label="净值记录"
          value={formatCompactNumber(stats.nav_total_records)}
          note={`最新净值：${stats.nav_latest_date || '暂无数据'}`}
          icon={<DatabaseOutlined />}
          color="#d99614"
        />
      </div>

      <div className="dashboard-grid">
        <Card
          className="soft-card"
          title={
            <Space>
              <SafetyCertificateOutlined />
              数据健康与质量
              <Tag color={dataFreshness.tagColor}>{dataFreshness.label}</Tag>
            </Space>
          }
          extra={<Button size="small" onClick={() => navigate('/funds')}>查看质量概览</Button>}
        >
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <div className="status-line">
              <div className="status-icon"><CalendarOutlined /></div>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>净值更新至</Text>
                <div style={{ fontSize: 18, fontWeight: 700 }}>
                  {stats.nav_latest_date
                    ? dayjs(stats.nav_latest_date).format('YYYY年MM月DD日')
                    : '暂无数据'}
                </div>
              </div>
            </div>

            <Alert type={dataFreshness.status} showIcon message={dataFreshness.description} />

            <Space size={6} wrap>
              <Tag color="green">良好 {navQualityOverview?.status_counts?.good || 0}</Tag>
              <Tag color="orange">需关注 {navQualityOverview?.status_counts?.warning || 0}</Tag>
              <Tag color="red">较差 {navQualityOverview?.status_counts?.poor || 0}</Tag>
              {qualityLoading && <Tag color="blue">质量概览加载中</Tag>}
            </Space>

            <Alert type={qualitySummary.type} showIcon message={qualitySummary.label} description={qualitySummary.description} />

            {qualitySamples.length > 0 ? (
              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                <Text type="secondary" style={{ fontSize: 12 }}>需优先复核样本</Text>
                <Space size={6} wrap>
                  {qualitySamples.map((item) => (
                    <Tag key={item.fund_code} color={QUALITY_STATUS_COLORS[item.status] || 'default'}>
                      <a onClick={() => navigate(`/funds/${item.fund_code}`)}>{item.fund_code}</a>
                      {' '}{QUALITY_STATUS_LABELS[item.status] || item.status}
                      {item.last_nav_date ? ` · ${item.last_nav_date}` : ''}
                      {item.max_gap_days > 0 ? ` · 缺口${item.max_gap_days}天` : ''}
                      {item.spike_count > 0 ? ` · 跳变${item.spike_count}次` : ''}
                    </Tag>
                  ))}
                </Space>
              </Space>
            ) : (
              <Text type="secondary" style={{ fontSize: 12 }}>
                暂无需重点复核的样本。关键分析前仍建议确认数据日期、复权口径和基金成立时间。
              </Text>
            )}
          </Space>
        </Card>

        <Card className="soft-card" title="下一步做什么">
          <div className="action-card-list">
            <ActionCard
              to="/discovery"
              icon={<RocketOutlined />}
              title="发现候选基金"
              description="查看排行榜、4433 筛选和截面评分。"
            />
            <ActionCard
              to="/funds"
              icon={<FundOutlined />}
              title="检索基金详情"
              description="补齐净值、查看质量、穿透持仓。"
            />
            <ActionCard
              to="/advisor"
              icon={<BulbOutlined />}
              title="检查当前组合"
              description="导入持仓后查看风险和调仓参考。"
            />
            <ActionCard
              to="/backtests"
              icon={<LineChartOutlined />}
              title="验证研究思路"
              description="用历史数据验证收益、回撤和稳定性。"
            />
          </div>

          <Alert
            style={{ marginTop: 14 }}
            type="info"
            showIcon
            message="推荐流程"
            description="先确认数据质量，再筛选基金，最后用回测和组合检查交叉验证，避免只看短期收益做判断。"
          />
        </Card>
      </div>

      <Card
        className="soft-card"
        title="最近回测"
        extra={stats.recent_backtests.length > 0 && (
          <Button type="link" onClick={() => navigate('/backtests')}>
            全部回测 <ArrowRightOutlined />
          </Button>
        )}
      >
        {stats.recent_backtests.length > 0 ? (
          <Table
            dataSource={stats.recent_backtests}
            rowKey="run_id"
            pagination={false}
            size="small"
            columns={[
              {
                title: '策略',
                dataIndex: 'strategy_name',
                key: 'strategy_name',
                render: (name: string | null, record: RecentBacktest) => (
                  <Link to={`/backtests/${record.run_id}`}>
                    {name || `回测 #${record.run_id}`}
                  </Link>
                ),
              },
              {
                title: '状态',
                dataIndex: 'status',
                key: 'status',
                render: (status: string) => {
                  const colorMap: Record<string, string> = {
                    done: 'green',
                    running: 'blue',
                    pending: 'default',
                    failed: 'red',
                  };
                  const labelMap: Record<string, string> = {
                    done: '完成',
                    running: '运行中',
                    pending: '排队中',
                    failed: '失败',
                  };
                  return <Tag color={colorMap[status] ?? 'default'}>{labelMap[status] ?? status}</Tag>;
                },
              },
              {
                title: '总收益',
                dataIndex: 'total_return',
                key: 'total_return',
                render: (val: number | null) => {
                  if (val == null) return '-';
                  const pct = (val * 100).toFixed(2);
                  const color = val >= 0 ? '#cf1322' : '#3f8600';
                  return <span style={{ color }}>{val >= 0 ? '+' : ''}{pct}%</span>;
                },
              },
              {
                title: 'Sharpe',
                dataIndex: 'sharpe',
                key: 'sharpe',
                render: (val: number | null) => val != null ? val.toFixed(2) : '-',
              },
              {
                title: '最大回撤',
                dataIndex: 'max_drawdown',
                key: 'max_drawdown',
                render: (val: number | null) => {
                  if (val == null) return '-';
                  return <span style={{ color: '#3f8600' }}>-{(val * 100).toFixed(2)}%</span>;
                },
              },
              {
                title: '完成时间',
                dataIndex: 'finished_at',
                key: 'finished_at',
                render: (val: string | null) =>
                  val ? dayjs(val).format('MM-DD HH:mm') : '-',
              },
            ]}
          />
        ) : (
          <div className="empty-guide">
            <Empty description="暂无回测记录，可先从基金发现或策略管理开始验证个人研究思路" />
            <Space wrap style={{ marginTop: 12 }}>
              <Button icon={<RocketOutlined />} onClick={() => navigate('/discovery')}>先发现基金</Button>
              <Button type="primary" icon={<LineChartOutlined />} onClick={() => navigate('/backtests')}>进入回测分析</Button>
            </Space>
          </div>
        )}
      </Card>
    </div>
  );
}
