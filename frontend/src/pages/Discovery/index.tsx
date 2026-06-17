import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Typography,
  Card,
  Table,
  Tag,
  Row,
  Col,
  Select,
  Statistic,
  Button,
  Space,
  Empty,
  Alert,
  Tooltip,
  Tabs,
  InputNumber,
  Form,
  Spin,
} from 'antd';
import {
  RocketOutlined,
  ReloadOutlined,
  FundOutlined,
  RiseOutlined,
  BarChartOutlined,
  CalendarOutlined,
  FilterOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table';
import {
  useRankingList,
  useDiscoveryStats,
  useTriggerDiscovery,
  useFilter4433,
  type RankingItem,
  type RankingListParams,
  type Fund4433Item,
} from '@/api/discovery';
import {
  useCrossSectionalScoring,
  type CrossSectionalFundScore,
} from '@/api/advisor';

const { Text } = Typography;

/** 排序维度选项 */
const SORT_METRIC_OPTIONS = [
  { label: '全部维度', value: '' },
  { label: '近6月涨幅', value: '6yzf' },
  { label: '近1年涨幅', value: '1nzf' },
  { label: '近3年涨幅', value: '3nzf' },
];

/** 基金类型选项 */
const FUND_TYPE_OPTIONS = [
  { label: '全部类型', value: '' },
  { label: '股票型', value: 'stock' },
  { label: '混合型', value: 'mixed' },
  { label: '指数型', value: 'index' },
  { label: '债券型', value: 'bond' },
];

/** 排序维度中文映射 */
const METRIC_LABELS: Record<string, string> = {
  '6yzf': '近6月',
  '1nzf': '近1年',
  '3nzf': '近3年',
  '1yzf': '近1月',
  '3yzf': '近3月',
};

/** 基金类型颜色 */
const TYPE_COLORS: Record<string, string> = {
  stock: 'red',
  mixed: 'purple',
  index: 'gold',
  bond: 'blue',
};

const TYPE_LABELS: Record<string, string> = {
  stock: '股票型',
  mixed: '混合型',
  index: '指数型',
  bond: '债券型',
};

/** 格式化收益率（东方财富返回的数据已经是百分比形式，如 5.04 表示 5.04%） */
function formatReturn(value: string | null): React.ReactNode {
  if (!value) return '-';
  const num = parseFloat(value);
  if (isNaN(num)) return '-';
  const pct = num.toFixed(2);
  const color = num >= 0 ? '#cf1322' : '#3f8600';
  return <span style={{ color }}>{num >= 0 ? '+' : ''}{pct}%</span>;
}

function formatScore(value: number | null | undefined): React.ReactNode {
  if (value == null || Number.isNaN(value)) return '-';
  return `${(value * 100).toFixed(1)}`;
}

function scoreTag(value: number | null | undefined): React.ReactNode {
  if (value == null || Number.isNaN(value)) return '-';
  const color = value >= 0.75 ? 'green' : value >= 0.5 ? 'blue' : value >= 0.25 ? 'gold' : 'default';
  return <Tag color={color}>{formatScore(value)}</Tag>;
}

export function DiscoveryPage() {
  const navigate = useNavigate();
  const [params, setParams] = useState<RankingListParams>({
    page: 1,
    page_size: 20,
    sort_metric: '6yzf',
  });

  const { data: rankings, isLoading, isError, error } = useRankingList(params);
  const { data: stats } = useDiscoveryStats();
  const triggerMutation = useTriggerDiscovery();

  const columns: ColumnsType<RankingItem> = [
    {
      title: '排名',
      dataIndex: 'rank_position',
      key: 'rank_position',
      width: 70,
      render: (rank: number) => (
        <Text strong={rank <= 3} type={rank <= 3 ? 'danger' : undefined}>
          {rank}
        </Text>
      ),
    },
    {
      title: '基金代码',
      dataIndex: 'fund_code',
      key: 'fund_code',
      width: 100,
      render: (code: string) => (
        <a onClick={() => navigate(`/funds/${code}`)}>{code}</a>
      ),
    },
    {
      title: '基金名称',
      dataIndex: 'fund_name',
      key: 'fund_name',
      ellipsis: true,
    },
    {
      title: '类型',
      dataIndex: 'fund_type',
      key: 'fund_type',
      width: 80,
      render: (type: string | null) =>
        type ? (
          <Tag color={TYPE_COLORS[type] || 'default'}>
            {TYPE_LABELS[type] || type}
          </Tag>
        ) : '-',
    },
    {
      title: '排序维度',
      dataIndex: 'sort_metric',
      key: 'sort_metric',
      width: 90,
      render: (metric: string) => (
        <Tag>{METRIC_LABELS[metric] || metric}</Tag>
      ),
    },
    {
      title: '日涨幅',
      dataIndex: 'daily_return',
      key: 'daily_return',
      width: 90,
      render: formatReturn,
    },
    {
      title: '近1月',
      dataIndex: 'monthly_return',
      key: 'monthly_return',
      width: 90,
      render: formatReturn,
    },
    {
      title: '近3月',
      dataIndex: 'quarterly_return',
      key: 'quarterly_return',
      width: 90,
      render: formatReturn,
    },
    {
      title: '近6月',
      dataIndex: 'half_year_return',
      key: 'half_year_return',
      width: 90,
      render: formatReturn,
    },
    {
      title: '近1年',
      dataIndex: 'yearly_return',
      key: 'yearly_return',
      width: 90,
      render: formatReturn,
    },
  ];

  const handleTableChange = (pagination: TablePaginationConfig) => {
    setParams((prev) => ({
      ...prev,
      page: pagination.current || 1,
      page_size: pagination.pageSize || 20,
    }));
  };

  if (isError) {
    return (
      <div className="page-shell">
        <Alert
          type="error"
          message="基金发现加载失败"
          description={
            error instanceof Error
              ? error.message
              : '获取排名数据时发生错误，请稍后重试。'
          }
          showIcon
        />
      </div>
    );
  }

  return (
    <div className="page-shell">
      <section className="section-hero">
        <div className="section-hero-content">
          <div className="page-eyebrow">
            <RocketOutlined /> Discovery Radar
          </div>
          <h2>基金发现与候选池筛选</h2>
          <p>
            用排行榜快照、4433 筛选和个人截面评分建立候选池。发现结果只是研究入口，
            仍需要结合数据质量、持仓穿透和回测验证。
          </p>
          <div className="section-hero-actions">
            <Tooltip title="手动触发一次排行榜抓取和基金发现">
              <Button
                type="primary"
                icon={<ReloadOutlined />}
                loading={triggerMutation.isPending}
                onClick={() => triggerMutation.mutate()}
              >
                立即发现
              </Button>
            </Tooltip>
            <Button icon={<FilterOutlined />}>4433 筛选</Button>
            <Button icon={<SafetyCertificateOutlined />}>个人评分</Button>
          </div>
        </div>
      </section>

      <Card className="soft-card page-tabs">
        <Tabs className="page-tabs" defaultActiveKey="rankings" items={[
        {
          key: 'rankings',
          label: '排行榜',
          children: (
            <>
              {stats && (
                <div className="mini-stat-grid" style={{ marginBottom: 16 }}>
                  <Card className="mini-stat-card">
                    <div className="mini-stat-label">跟踪基金数</div>
                    <div className="mini-stat-value"><FundOutlined /> {stats.total_funds_tracked}</div>
                    <div className="mini-stat-meta">当前本地持续跟踪的基金数量</div>
                  </Card>
                  <Card className="mini-stat-card">
                    <div className="mini-stat-label">发现新增</div>
                    <div className="mini-stat-value" style={{ color: '#1f9d68' }}><RiseOutlined /> {stats.funds_from_discovery}</div>
                    <div className="mini-stat-meta">由发现任务补充进入候选池</div>
                  </Card>
                  <Card className="mini-stat-card">
                    <div className="mini-stat-label">排名中基金</div>
                    <div className="mini-stat-value"><BarChartOutlined /> {stats.unique_funds_in_rankings}</div>
                    <div className="mini-stat-meta">最新排行榜快照覆盖数量</div>
                  </Card>
                  <Card className="mini-stat-card">
                    <div className="mini-stat-label">最新快照</div>
                    <div className="mini-stat-value" style={{ fontSize: 18 }}><CalendarOutlined /> {stats.latest_snapshot_date || '暂无'}</div>
                    <div className="mini-stat-meta">建议确认日期后再解读排名</div>
                  </Card>
                </div>
              )}

              <Card className="filter-card" style={{ marginBottom: 16 }}>
                <Space wrap>
                  <Select
                    style={{ width: 140 }}
                    placeholder="排序维度"
                    defaultValue="6yzf"
                    options={SORT_METRIC_OPTIONS}
                    allowClear
                    onChange={(value) => setParams((prev) => ({ ...prev, sort_metric: value || undefined, page: 1 }))}
                  />
                  <Select
                    style={{ width: 120 }}
                    placeholder="基金类型"
                    options={FUND_TYPE_OPTIONS}
                    allowClear
                    onChange={(value) => setParams((prev) => ({ ...prev, fund_type: value || undefined, page: 1 }))}
                  />
                </Space>
              </Card>

              <Card className="soft-card">
                <Table<RankingItem>
                  columns={columns}
                  dataSource={rankings?.items || []}
                  rowKey={(record) => `${record.fund_code}-${record.sort_metric}`}
                  loading={isLoading}
                  pagination={{
                    current: rankings?.page || 1,
                    pageSize: params.page_size || 20,
                    total: rankings?.total || 0,
                    showSizeChanger: true,
                    showQuickJumper: true,
                    showTotal: (total) => `共 ${total} 条`,
                    pageSizeOptions: ['20', '50', '100'],
                  }}
                  onChange={handleTableChange}
                  onRow={(record) => ({
                    onClick: () => navigate(`/funds/${record.fund_code}`),
                    style: { cursor: 'pointer' },
                  })}
                  locale={{ emptyText: <Empty description="暂无排名数据，请先触发发现任务" /> }}
                  size="middle"
                  scroll={{ x: 900 }}
                />
              </Card>
            </>
          ),
        },
        {
          key: '4433',
          label: <><FilterOutlined /> 筛选</>,
          children: <Filter4433Tab />,
        },
        {
          key: 'personal-score',
          label: <><SafetyCertificateOutlined /> 个人评分</>,
          children: <PersonalScoreTab />,
        },
        ]} />
      </Card>
    </div>
  );
}


// ---------------------------------------------------------------------------
// 4433 筛选 Tab
// ---------------------------------------------------------------------------

function Filter4433Tab() {
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const filter4433 = useFilter4433();

  const handleSearch = async () => {
    const values = await form.validateFields();
    filter4433.mutate({
      fund_type: values.fund_type || undefined,
      year1_percentile: (values.year1_percentile || 25) / 100,
      month6_percentile: (values.month6_percentile || 33) / 100,
      month3_percentile: (values.month3_percentile || 33) / 100,
      min_inception_years: values.min_inception_years,
    });
  };

  const columns: ColumnsType<Fund4433Item> = [
    {
      title: '基金代码',
      dataIndex: 'fund_code',
      key: 'fund_code',
      width: 100,
      render: (code: string) => <a onClick={() => navigate(`/funds/${code}`)}>{code}</a>,
    },
    {
      title: '基金名称',
      dataIndex: 'fund_name',
      key: 'fund_name',
      ellipsis: true,
    },
    {
      title: '类型',
      dataIndex: 'fund_type',
      key: 'fund_type',
      width: 80,
      render: (type: string | null) => type ? <Tag>{type}</Tag> : '-',
    },
    {
      title: '近1年排名',
      dataIndex: 'rank_1y',
      key: 'rank_1y',
      width: 100,
      render: (v: number | null) => v != null ? `前 ${(v * 100).toFixed(1)}%` : '-',
    },
    {
      title: '近6月排名',
      dataIndex: 'rank_6m',
      key: 'rank_6m',
      width: 100,
      render: (v: number | null) => v != null ? `前 ${(v * 100).toFixed(1)}%` : '-',
    },
    {
      title: '近3月排名',
      dataIndex: 'rank_3m',
      key: 'rank_3m',
      width: 100,
      render: (v: number | null) => v != null ? `前 ${(v * 100).toFixed(1)}%` : '-',
    },
    {
      title: '近1年收益',
      dataIndex: 'return_1y',
      key: 'return_1y',
      width: 100,
      render: (v: number | null) => {
        if (v == null) return '-';
        const pct = v.toFixed(2);
        return <span style={{ color: v >= 0 ? '#cf1322' : '#3f8600' }}>{v >= 0 ? '+' : ''}{pct}%</span>;
      },
    },
    {
      title: '近6月收益',
      dataIndex: 'return_6m',
      key: 'return_6m',
      width: 100,
      render: (v: number | null) => {
        if (v == null) return '-';
        const pct = v.toFixed(2);
        return <span style={{ color: v >= 0 ? '#cf1322' : '#3f8600' }}>{v >= 0 ? '+' : ''}{pct}%</span>;
      },
    },
  ];

  return (
    <div>
      <Card className="filter-card" style={{ marginBottom: 16 }}>
        <Form form={form} layout="inline" initialValues={{ year1_percentile: 25, month6_percentile: 33, month3_percentile: 33, min_inception_years: 3 }}>
          <Form.Item name="fund_type" label="基金类型">
            <Select style={{ width: 120 }} allowClear placeholder="全部" options={FUND_TYPE_OPTIONS} />
          </Form.Item>
          <Form.Item name="year1_percentile" label="近1年前">
            <InputNumber min={1} max={100} addonAfter="%" style={{ width: 100 }} />
          </Form.Item>
          <Form.Item name="month6_percentile" label="近6月前">
            <InputNumber min={1} max={100} addonAfter="%" style={{ width: 100 }} />
          </Form.Item>
          <Form.Item name="month3_percentile" label="近3月前">
            <InputNumber min={1} max={100} addonAfter="%" style={{ width: 100 }} />
          </Form.Item>
          <Form.Item name="min_inception_years" label="最小成立年限">
            <InputNumber min={0} max={20} style={{ width: 80 }} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" icon={<FilterOutlined />} onClick={handleSearch} loading={filter4433.isPending}>
              筛选
            </Button>
          </Form.Item>
        </Form>
      </Card>

      {filter4433.data && (
        <Card className="soft-card">
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={8}>
              <Statistic title="筛选基金总数" value={filter4433.data.total_screened} />
            </Col>
            <Col span={8}>
              <Statistic title="通过 4433" value={filter4433.data.passed_count} valueStyle={{ color: '#3f8600' }} />
            </Col>
            <Col span={8}>
              <Statistic title="通过率" value={filter4433.data.pass_rate * 100} precision={1} suffix="%" />
            </Col>
          </Row>

          <Table<Fund4433Item>
            columns={columns}
            dataSource={filter4433.data.funds}
            rowKey="fund_code"
            size="middle"
            scroll={{ x: 800 }}
            pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 只通过` }}
            onRow={(record) => ({
              onClick: () => navigate(`/funds/${record.fund_code}`),
              style: { cursor: 'pointer' },
            })}
          />
        </Card>
      )}

      {!filter4433.data && !filter4433.isPending && (
        <Card className="soft-card">
          <div className="empty-guide">
            <Empty description="点击「筛选」按钮开始 4433 法则筛选" />
          </div>
        </Card>
      )}

      {filter4433.isPending && (
        <Card className="soft-card">
          <div style={{ textAlign: 'center', padding: 60 }}>
            <Spin tip="正在筛选中..." />
          </div>
        </Card>
      )}
    </div>
  );
}

function PersonalScoreTab() {
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const scoring = useCrossSectionalScoring();

  const handleRun = async () => {
    const values = await form.validateFields();
    scoring.mutate({
      fund_type: values.fund_type || null,
      min_history_days: values.min_history_days || 252,
      top_n: values.top_n || 20,
    });
  };

  const columns: ColumnsType<CrossSectionalFundScore> = [
    {
      title: '基金代码',
      dataIndex: 'fund_code',
      key: 'fund_code',
      width: 100,
      render: (code: string) => <a onClick={() => navigate(`/funds/${code}`)}>{code}</a>,
    },
    {
      title: '基金名称',
      dataIndex: 'fund_name',
      key: 'fund_name',
      ellipsis: true,
    },
    {
      title: '个人研究评分',
      dataIndex: 'composite_rank',
      key: 'composite_rank',
      width: 130,
      align: 'right',
      render: (v: number) => <Text strong>{formatScore(v)}</Text>,
      sorter: (a, b) => a.composite_rank - b.composite_rank,
      defaultSortOrder: 'descend',
    },
    {
      title: '收益质量',
      key: 'return_quality',
      width: 150,
      render: (_, record) => (
        <Space size={4} wrap>
          <Tooltip title="Alpha 持续性分位">A {scoreTag(record.ranks.alpha)}</Tooltip>
          <Tooltip title="Sharpe 持续性分位">S {scoreTag(record.ranks.sharpe)}</Tooltip>
        </Space>
      ),
    },
    {
      title: '风险控制',
      key: 'risk_control',
      width: 110,
      render: (_, record) => scoreTag(record.ranks.drawdown),
    },
    {
      title: '稳定性',
      key: 'consistency',
      width: 100,
      render: (_, record) => scoreTag(record.ranks.consistency),
    },
    {
      title: '成本/规模',
      key: 'cost_size',
      width: 150,
      render: (_, record) => (
        <Space size={4} wrap>
          <Tooltip title="费率分位，越高代表相对更优">费 {scoreTag(record.ranks.fee)}</Tooltip>
          <Tooltip title="规模分位，按模型现有口径展示">规 {scoreTag(record.ranks.size)}</Tooltip>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="个人基金研究评分，仅用于候选池排序"
        description="本评分复用截面因子模型，在同类基金内做分位比较；分数不代表未来收益，不构成投资建议或交易指令。评分依赖 NAV、费率、规模等数据质量，请结合基金详情与回测验证独立判断。"
      />

      <Card className="filter-card" style={{ marginBottom: 16 }}>
        <Form form={form} layout="inline" initialValues={{ fund_type: '', min_history_days: 252, top_n: 20 }}>
          <Form.Item name="fund_type" label="基金类型">
            <Select style={{ width: 120 }} options={FUND_TYPE_OPTIONS} />
          </Form.Item>
          <Form.Item name="min_history_days" label="最少历史天数">
            <InputNumber min={120} max={1500} step={30} style={{ width: 120 }} />
          </Form.Item>
          <Form.Item name="top_n" label="返回数量">
            <InputNumber min={5} max={50} step={5} style={{ width: 100 }} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" icon={<SafetyCertificateOutlined />} onClick={handleRun} loading={scoring.isPending}>
              生成评分
            </Button>
          </Form.Item>
        </Form>
      </Card>

      {scoring.data && (
        <Space direction="vertical" style={{ width: '100%' }} size={16}>
          <Row gutter={16}>
            <Col span={8}>
              <Card size="small">
                <Statistic title="参与评估" value={scoring.data.n_funds_evaluated} />
              </Card>
            </Col>
            <Col span={8}>
              <Card size="small">
                <Statistic title="数据合格" value={scoring.data.n_funds_qualified} />
              </Card>
            </Col>
            <Col span={8}>
              <Card size="small">
                <Statistic title="平均 IC" value={scoring.data.avg_ic ?? '-'} precision={4} />
              </Card>
            </Col>
          </Row>

          {scoring.data.warnings.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message="评分提示"
              description={scoring.data.warnings.slice(0, 5).join('；')}
            />
          )}

          <Card className="soft-card">
            <Table<CrossSectionalFundScore>
              columns={columns}
              dataSource={scoring.data.fund_scores || []}
              rowKey="fund_code"
              size="middle"
              scroll={{ x: 900 }}
              pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 只基金` }}
              onRow={(record) => ({
                onClick: () => navigate(`/funds/${record.fund_code}`),
                style: { cursor: 'pointer' },
              })}
            />
          </Card>

          <Alert
            type="info"
            showIcon
            message="评分方法"
            description={scoring.data.methodology}
          />
        </Space>
      )}

      {!scoring.data && !scoring.isPending && (
        <Card className="soft-card">
          <div className="empty-guide">
            <Empty description="点击「生成评分」查看同类基金截面研究评分" />
          </div>
        </Card>
      )}

      {scoring.isPending && (
        <Card className="soft-card">
          <div style={{ textAlign: 'center', padding: 60 }}>
            <Spin tip="正在生成个人研究评分..." />
          </div>
        </Card>
      )}
    </div>
  );
}
