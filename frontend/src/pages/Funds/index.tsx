import { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Typography,
  Input,
  Select,
  Table,
  Card,
  Tag,
  Row,
  Col,
  Empty,
  Alert,
  Button,
  List,
  Spin,
  Space,
  message,
  Tabs,
  Progress,
  Statistic,
  Popconfirm,
  Tooltip,
  DatePicker,
} from 'antd';
import {
  SearchOutlined,
  CloudDownloadOutlined,
  CheckCircleOutlined,
  LoadingOutlined,
  PieChartOutlined,
  StockOutlined,
  FundOutlined,
  DeleteOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';

import ReactECharts from 'echarts-for-react';
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table';
import {
  useFundList,
  useOnlineSearch,
  ingestFund,
  deleteFund,
  useValuation,
  useFundNavQualityOverview,
  type FundSummary,
  type FundListParams,
  type ValuationItem,
  type NavQualityOverviewItem,
  type NavQualityOverviewParams,
} from '@/api/funds';
import {
  usePenetrateHoldings,
  useHoldingsSimilarity,
  useFundsByStock,
  type StockExposureItem,
  type SimilarityItem,
} from '@/api/holdings';
import { useQueryClient } from '@tanstack/react-query';
import { useIngestStore, startIngestPolling, isPolling } from '@/stores/ingest';
import { FUND_TYPE_OPTIONS, fundTypeColor, fundTypeLabel } from '@/utils/fundType';

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

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

function formatTabName(activeTab: string) {
  const names: Record<string, string> = {
    search: '搜索',
    holdings: '持仓',
    'stock-fund': '选基',
    valuation: '分位',
    quality: '质量',
  };
  return names[activeTab] || activeTab;
}

export function FundsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useState<FundListParams>({
    page: 1,
    page_size: 20,
  });
  const [activeTab, setActiveTab] = useState('search');

  // 使用全局 store 保持采集状态和搜索关键词
  const {
    tasks: ingestingTasks,
    onlineKeyword,
    showOnlineResults,
    setOnlineKeyword,
    setShowOnlineResults,
    setTask,
  } = useIngestStore();

  const { data, isLoading, isError, error } = useFundList(params);
  const {
    data: onlineData,
    isLoading: onlineLoading,
    isError: onlineIsError,
    error: onlineError,
  } = useOnlineSearch(onlineKeyword);
  const queryClient = useQueryClient();

  const handleIngest = useCallback(async (code: string) => {
    setTask(code, {
      taskId: '',
      state: 'STARTED',
      progress: '正在提交采集任务...',
      fundCode: code,
      startedAt: Date.now(),
    });
    try {
      const result = await ingestFund(code);
      // 刷新本地列表
      queryClient.invalidateQueries({ queryKey: ['funds'] });
      queryClient.invalidateQueries({ queryKey: ['onlineSearch'] });

      if (result.status === 'pending' && result.task_id) {
        // Celery 异步任务，开始轮询（轮询在全局 store 中管理，不随组件卸载而停止）
        message.info(`${result.fund_name || code} 全量采集任务已提交`);
        setTask(code, {
          taskId: result.task_id,
          state: 'PENDING',
          progress: '正在采集净值数据...',
          fundCode: code,
          fundName: result.fund_name || undefined,
          startedAt: Date.now(),
        });
        if (!isPolling(code)) {
          startIngestPolling(code, result.task_id, {
            onSuccess: (c, res) => {
              const recordsInserted = (res?.records_inserted as number) ?? 0;
              if (recordsInserted > 0) {
                message.success(`基金 ${c} 净值采集完成，共 ${recordsInserted} 条记录`);
              } else {
                message.warning(`基金 ${c} 采集完成但无新数据`);
              }
              queryClient.invalidateQueries({ queryKey: ['funds'] });
              queryClient.invalidateQueries({ queryKey: ['onlineSearch'] });
            },
            onFailure: (c, progress) => {
              message.error(`基金 ${c} 采集失败: ${progress}`);
            },
          });
        }
      } else if (result.status === 'success') {
        message.success(result.message);
        setTask(code, {
          taskId: '',
          state: 'SUCCESS',
          progress: result.message,
          fundCode: code,
          fundName: result.fund_name || undefined,
          startedAt: Date.now(),
        });
        queryClient.invalidateQueries({ queryKey: ['funds'] });
        queryClient.invalidateQueries({ queryKey: ['onlineSearch'] });
      } else {
        message.error(result.message);
        setTask(code, {
          taskId: '',
          state: 'FAILURE',
          progress: result.message,
          fundCode: code,
          fundName: result.fund_name || undefined,
          startedAt: Date.now(),
        });
        queryClient.invalidateQueries({ queryKey: ['funds'] });
        queryClient.invalidateQueries({ queryKey: ['onlineSearch'] });
      }
    } catch {
      message.error(`采集基金 ${code} 失败`);
      setTask(code, {
        taskId: '',
        state: 'FAILURE',
        progress: '采集失败',
        fundCode: code,
        startedAt: Date.now(),
      });
    }
  }, [setTask, queryClient]);

  /** 删除本地基金 */
  const handleDeleteFund = async (code: string, name: string) => {
    try {
      const result = await deleteFund(code);
      message.success(result.message);
      // 刷新列表
      queryClient.invalidateQueries({ queryKey: ['funds'] });
    } catch {
      message.error(`删除基金 ${name}(${code}) 失败`);
    }
  };

  // 计算是否有正在进行的采集任务
  const hasActiveIngestTasks = Array.from(ingestingTasks.values()).some(
    (t) => t.state === 'PENDING' || t.state === 'STARTED',
  );
  const activeTaskCount = Array.from(ingestingTasks.values()).filter(
    (t) => t.state === 'PENDING' || t.state === 'STARTED',
  ).length;

  /** 渲染采集按钮（含状态） */
  const renderIngestButton = (code: string, navStatus: string) => {
    const task = ingestingTasks.get(code);
    if (task) {
      if (task.state === 'SUCCESS') {
        // 采集完成：显示"全量采集"标签 + 重新采集按钮
        return (
          <Space>
            <Tag icon={<CheckCircleOutlined />} color="success">全量采集</Tag>
            <Button size="small" icon={<CloudDownloadOutlined />} onClick={() => handleIngest(code)}>重新采集</Button>
          </Space>
        );
      }
      if (task.state === 'FAILURE') {
        return (
          <Space>
            <Tag color="error">采集失败</Tag>
            <Button size="small" icon={<CloudDownloadOutlined />} onClick={() => handleIngest(code)}>重试</Button>
          </Space>
        );
      }
      // 正在采集中
      return (
        <Tag icon={<LoadingOutlined />} color="processing">
          {task.progress}
        </Tag>
      );
    }

    if (navStatus === 'full') {
      return (
        <Space>
          <Tag icon={<CheckCircleOutlined />} color="success">全量采集</Tag>
          <Button size="small" icon={<CloudDownloadOutlined />} onClick={() => handleIngest(code)}>重新采集</Button>
        </Space>
      );
    }

    return (
      <Button
        type={navStatus === 'partial' ? 'default' : 'primary'}
        size="small"
        icon={<CloudDownloadOutlined />}
        onClick={() => handleIngest(code)}
      >
        {navStatus === 'partial' ? '全量采集' : '一键采集'}
      </Button>
    );
  };

  const columns: ColumnsType<FundSummary> = [
    {
      title: '基金代码',
      dataIndex: 'code',
      key: 'code',
      width: 120,
      render: (code: string) => (
        <a onClick={() => navigate(`/funds/${code}`)}>{code}</a>
      ),
    },
    {
      title: '基金名称',
      dataIndex: 'name',
      key: 'name',
      ellipsis: true,
    },
    {
      title: '类型',
      dataIndex: 'fund_type',
      key: 'fund_type',
      width: 100,
      render: (type: string | null) =>
        type ? (
          <Tag color={fundTypeColor(type)}>
            {fundTypeLabel(type)}
          </Tag>
        ) : (
          '-'
        ),
    },
    {
      title: '管理费率',
      dataIndex: 'management_fee',
      key: 'management_fee',
      width: 100,
      render: (fee: string | null) =>
        fee ? `${(parseFloat(fee) * 100).toFixed(2)}%` : '-',
    },
    {
      title: '成立日期',
      dataIndex: 'inception_date',
      key: 'inception_date',
      width: 120,
      render: (date: string | null) => date || '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (status: string) => (
        <Tag color={status === 'active' ? 'green' : 'default'}>
          {status === 'active' ? '正常' : status}
        </Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_, record: FundSummary) => (
        <Popconfirm
          title="删除本地基金"
          description={`确定要删除 ${record.name}(${record.code}) 吗？删除后将清除该基金所有本地数据，且后续每日更新不再更新该基金。`}
          onConfirm={(e) => { e?.stopPropagation(); handleDeleteFund(record.code, record.name); }}
          onCancel={(e) => e?.stopPropagation()}
          okText="确定删除"
          cancelText="取消"
          okButtonProps={{ danger: true }}
        >
          <Button
            type="text"
            danger
            size="small"
            icon={<DeleteOutlined />}
            onClick={(e) => e.stopPropagation()}
          />
        </Popconfirm>
      ),
    },
  ];

  const handleTableChange = (pagination: TablePaginationConfig) => {
    setParams((prev) => ({
      ...prev,
      page: pagination.current || 1,
      page_size: pagination.pageSize || 20,
    }));
  };

  const handleKeywordChange = (value: string) => {
    setParams((prev) => ({
      ...prev,
      keyword: value || undefined,
      page: 1,
    }));
  };

  const handleTypeChange = (value: string) => {
    setParams((prev) => ({
      ...prev,
      fund_type: value || undefined,
      page: 1,
    }));
  };

  const handleCompanyChange = (value: string) => {
    setParams((prev) => ({
      ...prev,
      company_id: value || undefined,
      page: 1,
    }));
  };

  const handleOnlineSearch = (value: string) => {
    if (value.trim()) {
      setOnlineKeyword(value.trim());
      setShowOnlineResults(true);
    } else {
      setShowOnlineResults(false);
    }
  };

  if (isError) {
    return (
      <div className="page-shell">
        <Title level={3}>基金检索</Title>
        <Alert
          type="error"
          message="加载失败"
          description={
            error instanceof Error
              ? error.message
              : '获取基金列表时发生错误，请稍后重试。'
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
            <SearchOutlined /> Fund Research
          </div>
          <h2>基金检索与数据治理</h2>
          <p>
            在一个页面内完成本地检索、在线采集、持仓穿透、净值分位和数据质量检查。
            先确认数据质量，再做研究和回测，避免把脏数据当成信号。
          </p>
          <div className="section-hero-actions">
            <Button type="primary" icon={<SearchOutlined />} onClick={() => setActiveTab('search')}>
              基金搜索
            </Button>
            <Button icon={<PieChartOutlined />} onClick={() => setActiveTab('holdings')}>
              持仓穿透
            </Button>
            <Button icon={<SafetyCertificateOutlined />} onClick={() => setActiveTab('quality')}>
              数据质量
            </Button>
          </div>
        </div>
      </section>

      <div className="mini-stat-grid" style={{ marginBottom: 8 }}>
        <Card className="mini-stat-card">
          <div className="mini-stat-label">本地基金</div>
          <div className="mini-stat-value">{data?.total || 0}</div>
          <div className="mini-stat-meta">本地基金库与列表分页总量</div>
        </Card>
        <Card className="mini-stat-card">
          <div className="mini-stat-label">在线结果</div>
          <div className="mini-stat-value">{showOnlineResults ? (onlineData?.results.length || 0) : 0}</div>
          <div className="mini-stat-meta">在线采集搜索命中的基金数量</div>
        </Card>
        <Card className="mini-stat-card">
          <div className="mini-stat-label">采集中</div>
          <div className="mini-stat-value">{activeTaskCount}</div>
          <div className="mini-stat-meta">当前仍在提交或轮询中的任务</div>
        </Card>
        <Card className="mini-stat-card">
          <div className="mini-stat-label">当前页签</div>
          <div className="mini-stat-value">{formatTabName(activeTab)}</div>
          <div className="mini-stat-meta">先看数据质量，再进入研究动作</div>
        </Card>
      </div>

      <Card className="soft-card page-tabs">
        <Tabs className="page-tabs" activeKey={activeTab} onChange={setActiveTab} items={[
          {
            key: 'search',
            label: '基金搜索',
            children: (
              <>
                <Alert
                  type="info"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="分析前建议先检查数据质量"
                  description={(
                    <Space direction="vertical" size={8}>
                      <Text>基金筛选、净值分位、回测和组合检查都依赖 NAV 覆盖率、复权覆盖率和异常跳变状态。若结果异常，请先到数据质量页签定位问题。</Text>
                      <Button size="small" icon={<SafetyCertificateOutlined />} onClick={() => setActiveTab('quality')}>
                        查看数据质量概览
                      </Button>
                    </Space>
                  )}
                />

                <Card className="filter-card" style={{ marginBottom: 16 }}>
                  <Row gutter={[16, 16]}>
                    <Col xs={24} sm={12} md={8}>
                      <Input.Search
                        placeholder="本地搜索：输入代码或名称"
                        prefix={<SearchOutlined />}
                        allowClear
                        onSearch={handleKeywordChange}
                        enterButton="本地搜索"
                      />
                    </Col>
                    <Col xs={24} sm={12} md={8}>
                      <Input.Search
                        placeholder="在线搜索：输入基金代码或名称"
                        allowClear
                        defaultValue={onlineKeyword}
                        onSearch={handleOnlineSearch}
                        enterButton="在线搜索"
                        loading={onlineLoading}
                      />
                    </Col>
                    <Col xs={24} sm={12} md={4}>
                      <Select
                        style={{ width: '100%' }}
                        placeholder="基金类型"
                        options={FUND_TYPE_OPTIONS}
                        allowClear
                        onChange={handleTypeChange}
                      />
                    </Col>
                    <Col xs={24} sm={12} md={4}>
                      <Input
                        placeholder="基金公司ID"
                        allowClear
                        onChange={(e) => handleCompanyChange(e.target.value)}
                      />
                    </Col>
                  </Row>
                </Card>

                {(showOnlineResults || hasActiveIngestTasks) && (
                  <Card
                    className="soft-card"
                    title={`在线搜索结果：${onlineKeyword}`}
                    style={{ marginBottom: 16 }}
                    extra={
                      <Space>
                        {hasActiveIngestTasks && (
                          <Tag icon={<LoadingOutlined />} color="processing">
                            {activeTaskCount} 个任务进行中
                          </Tag>
                        )}
                        <Button type="link" onClick={() => setShowOnlineResults(false)}>关闭</Button>
                      </Space>
                    }
                  >
                    {onlineLoading ? (
                      <Spin tip="正在从天天基金搜索..." />
                    ) : onlineIsError ? (
                      <Alert
                        type="error"
                        showIcon
                        message="在线搜索失败"
                        description={onlineError instanceof Error ? onlineError.message : '在线搜索暂时不可用，请稍后重试。'}
                      />
                    ) : onlineData?.results.length ? (
                      <List
                        dataSource={onlineData.results}
                        renderItem={(item) => (
                          <List.Item actions={[
                            renderIngestButton(item.code, item.nav_status),
                          ]}>
                            <List.Item.Meta title={<Space><Text strong>{item.code}</Text><Text>{item.name}</Text>{item.fund_type && <Tag color={fundTypeColor(item.fund_type)}>{fundTypeLabel(item.fund_type)}</Tag>}{item.nav_status === 'partial' && <Tag color="orange">部分数据</Tag>}</Space>} />
                          </List.Item>
                        )}
                      />
                    ) : (
                      <Empty description="未找到匹配的基金" />
                    )}
                  </Card>
                )}

                <Card className="soft-card">
                  <Table<FundSummary>
                    columns={columns}
                    dataSource={data?.items || []}
                    rowKey="code"
                    loading={isLoading}
                    pagination={{
                      current: data?.page || 1,
                      pageSize: data?.page_size || 20,
                      total: data?.total || 0,
                      showSizeChanger: true,
                      showQuickJumper: true,
                      showTotal: (total) => `共 ${total} 条`,
                      pageSizeOptions: ['10', '20', '50', '100'],
                    }}
                    onChange={handleTableChange}
                    onRow={(record) => ({ onClick: () => navigate(`/funds/${record.code}`), style: { cursor: 'pointer' } })}
                    locale={{ emptyText: <Empty description="暂无基金数据，请先采集或使用在线搜索添加" /> }}
                    size="middle"
                  />
                </Card>
              </>
            ),
          },
          {
            key: 'holdings',
            label: <><PieChartOutlined /> 持仓穿透</>,
            children: <HoldingsTab />,
          },
          {
            key: 'stock-fund',
            label: <><StockOutlined /> 股票选基</>,
            children: <StockFundTab />,
          },
          {
            key: 'valuation',
            label: <><FundOutlined /> 净值分位</>,
            children: <ValuationTab />,
          },
          {
            key: 'quality',
            label: <><SafetyCertificateOutlined /> 数据质量</>,
            children: <NavQualityTab />,
          },
        ]} />
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 数据质量 Tab
// ---------------------------------------------------------------------------

function NavQualityTab() {
  const navigate = useNavigate();
  const [params, setParams] = useState<NavQualityOverviewParams>({
    page: 1,
    page_size: 20,
  });
  const { data, isLoading, isError, error } = useFundNavQualityOverview(params);

  const columns: ColumnsType<NavQualityOverviewItem> = [
    {
      title: '基金',
      key: 'fund',
      width: 210,
      render: (_, record) => (
        <Space direction="vertical" size={0}>
          <a onClick={() => navigate(`/funds/${record.fund_code}`)}>{record.fund_code}</a>
          <Text ellipsis style={{ maxWidth: 170 }}>{record.fund_name}</Text>
        </Space>
      ),
    },
    {
      title: '类型',
      dataIndex: 'fund_type',
      key: 'fund_type',
      width: 90,
      render: (type: string | null) => type ? <Tag color={fundTypeColor(type)}>{fundTypeLabel(type)}</Tag> : '-',
    },
    {
      title: '质量状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => <Tag color={QUALITY_STATUS_COLORS[status] || 'default'}>{QUALITY_STATUS_LABELS[status] || status}</Tag>,
    },
    {
      title: 'NAV 覆盖率',
      dataIndex: 'coverage_ratio',
      key: 'coverage_ratio',
      width: 130,
      render: (value: number) => <Progress percent={Math.round(value * 100)} size="small" />,
    },
    {
      title: '复权覆盖率',
      dataIndex: 'adj_nav_coverage_ratio',
      key: 'adj_nav_coverage_ratio',
      width: 130,
      render: (value: number, record) => (
        <Tooltip title={record.unit_nav_fallback_points > 0 ? `${record.unit_nav_fallback_points} 个点缺少 adj_nav，可能回退 unit_nav` : '检查区间内有效 NAV 均有 adj_nav'}>
          <Progress percent={Math.round(value * 100)} size="small" status={record.unit_nav_fallback_points > 0 ? 'exception' : 'normal'} />
        </Tooltip>
      ),
    },
    {
      title: '最新 NAV',
      dataIndex: 'last_nav_date',
      key: 'last_nav_date',
      width: 120,
      render: (value: string | null) => value || '-',
    },
    {
      title: '最大缺口',
      dataIndex: 'max_gap_days',
      key: 'max_gap_days',
      width: 100,
      render: (value: number) => value > 0 ? `${value} 天` : '-',
    },
    {
      title: '跳变',
      dataIndex: 'spike_count',
      key: 'spike_count',
      width: 90,
      render: (value: number, record) => value > 0 ? <Tag color="orange">{value} 次 / 阈值 {record.spike_threshold}</Tag> : <Tag color="green">0</Tag>,
    },
    {
      title: '主要问题',
      key: 'issues',
      render: (_, record) => record.issues.length ? (
        <Tooltip title={record.issues.slice(0, 5).map((issue) => issue.message).join('；')}>
          <Space wrap>
            {record.issues.slice(0, 3).map((issue) => (
              <Tag key={`${issue.issue_type}-${issue.trade_date || issue.start_date || ''}`} color={issue.severity === 'poor' ? 'red' : 'orange'}>
                {issue.issue_type}
              </Tag>
            ))}
            {record.issues.length > 3 && <Tag>+{record.issues.length - 3}</Tag>}
          </Space>
        </Tooltip>
      ) : <Text type="secondary">暂无明显问题</Text>,
    },
  ];

  if (isError) {
    return (
      <Alert
        type="error"
        showIcon
        message="数据质量概览加载失败"
        description={error instanceof Error ? error.message : '请稍后重试。'}
      />
    );
  }

  return (
    <div>
      <div className="mini-stat-grid" style={{ marginBottom: 16 }}>
        <Card className="mini-stat-card">
          <div className="mini-stat-label">良好</div>
          <div className="mini-stat-value" style={{ color: '#1f9d68' }}>{data?.status_counts?.good || 0}</div>
          <div className="mini-stat-meta">NAV 覆盖和复权覆盖暂未发现明显问题</div>
        </Card>
        <Card className="mini-stat-card">
          <div className="mini-stat-label">需关注</div>
          <div className="mini-stat-value" style={{ color: '#d99614' }}>{data?.status_counts?.warning || 0}</div>
          <div className="mini-stat-meta">建议分析前复核缺口、跳变和复权覆盖</div>
        </Card>
        <Card className="mini-stat-card">
          <div className="mini-stat-label">较差</div>
          <div className="mini-stat-value" style={{ color: '#d84a4a' }}>{data?.status_counts?.poor || 0}</div>
          <div className="mini-stat-meta">不建议直接用于筛选、回测或组合检查</div>
        </Card>
        <Card className="mini-stat-card">
          <div className="mini-stat-label">检查口径</div>
          <div className="mini-stat-value">NAV</div>
          <div className="mini-stat-meta">按基金类型阈值检查跳变、缺口与 adj_nav 覆盖率</div>
        </Card>
      </div>

      <Card className="filter-card" style={{ marginBottom: 16 }}>
        <Row gutter={[16, 16]}>
          <Col xs={24} md={6}>
            <Input.Search
              placeholder="代码或名称"
              allowClear
              onSearch={(value) => setParams((prev) => ({ ...prev, keyword: value || undefined, page: 1 }))}
            />
          </Col>
          <Col xs={24} md={5}>
            <Select
              style={{ width: '100%' }}
              placeholder="基金类型"
              options={FUND_TYPE_OPTIONS}
              allowClear
              onChange={(value) => setParams((prev) => ({ ...prev, fund_type: value || undefined, page: 1 }))}
            />
          </Col>
          <Col xs={24} md={5}>
            <Select
              style={{ width: '100%' }}
              placeholder="质量状态"
              allowClear
              options={[
                { label: '良好', value: 'good' },
                { label: '需关注', value: 'warning' },
                { label: '较差', value: 'poor' },
              ]}
              onChange={(value) => setParams((prev) => ({ ...prev, status: value || undefined, page: 1 }))}
            />
          </Col>
          <Col xs={24} md={8}>
            <RangePicker
              style={{ width: '100%' }}
              onChange={(dates) => setParams((prev) => ({
                ...prev,
                start_date: dates?.[0]?.format('YYYY-MM-DD'),
                end_date: dates?.[1]?.format('YYYY-MM-DD'),
                page: 1,
              }))}
            />
          </Col>
        </Row>
      </Card>

      <Card className="soft-card">
        <Table<NavQualityOverviewItem>
          columns={columns}
          dataSource={data?.items || []}
          rowKey="fund_code"
          loading={isLoading}
          pagination={{
            current: data?.page || 1,
            pageSize: data?.page_size || 20,
            total: data?.total || 0,
            showSizeChanger: true,
            showTotal: (total) => `共 ${total} 条`,
            pageSizeOptions: ['10', '20', '50', '100'],
          }}
          onChange={(pagination) => setParams((prev) => ({
            ...prev,
            page: pagination.current || 1,
            page_size: pagination.pageSize || 20,
          }))}
          scroll={{ x: 1100 }}
          size="middle"
        />
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 持仓穿透 Tab
// ---------------------------------------------------------------------------

function HoldingsTab() {
  const [codes, setCodes] = useState('');
  const penetrate = usePenetrateHoldings();
  const similarity = useHoldingsSimilarity();

  const handleAnalyze = () => {
    const fundCodes = codes.split(/[,，\s]+/).filter(Boolean);
    if (fundCodes.length < 1) { message.warning('请输入至少1只基金代码'); return; }
    penetrate.mutate({ fundCodes });
    if (fundCodes.length >= 2) {
      similarity.mutate(fundCodes);
    }
  };

  const stockColumns: ColumnsType<StockExposureItem> = [
    { title: '排名', key: 'rank', width: 60, render: (_, __, idx) => idx + 1 },
    { title: '股票代码', dataIndex: 'stock_code', key: 'stock_code', width: 100 },
    { title: '股票名称', dataIndex: 'stock_name', key: 'stock_name', width: 120 },
    { title: '等效权重', dataIndex: 'weight', key: 'weight', width: 100, render: (v: number) => `${(v * 100).toFixed(2)}%` },
    { title: '行业', dataIndex: 'industry', key: 'industry', width: 100 },
    { title: '持有基金', dataIndex: 'funds', key: 'funds', render: (funds: string[]) => funds.join(', ') },
  ];

  const simColumns: ColumnsType<SimilarityItem> = [
    { title: '基金A', dataIndex: 'fund_a', key: 'fund_a', width: 100 },
    { title: '基金B', dataIndex: 'fund_b', key: 'fund_b', width: 100 },
    { title: '相似度', dataIndex: 'cosine_similarity', key: 'sim', width: 120, render: (v: number) => <Progress percent={Math.round(v * 100)} size="small" status={v > 0.7 ? 'exception' : 'normal'} /> },
    { title: '重叠股票数', dataIndex: 'overlap_count', key: 'overlap', width: 100 },
  ];

  return (
    <div>
      <Card className="filter-card" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input
            placeholder="输入基金代码，多只用逗号分隔（如 000001,110011,519300）"
            style={{ width: 500, maxWidth: '100%' }}
            value={codes}
            onChange={(e) => setCodes(e.target.value)}
            onPressEnter={handleAnalyze}
          />
          <Button type="primary" icon={<PieChartOutlined />} onClick={handleAnalyze} loading={penetrate.isPending}>
            分析持仓
          </Button>
        </Space>
      </Card>

      {penetrate.data && (
        <>
          <div className="mini-stat-grid" style={{ marginBottom: 16 }}>
            <Card className="mini-stat-card"><Statistic title="底层股票数" value={penetrate.data.total_stocks} /></Card>
            <Card className="mini-stat-card"><Statistic title="前5集中度" value={penetrate.data.top5_concentration * 100} precision={1} suffix="%" /></Card>
            <Card className="mini-stat-card"><Statistic title="前10集中度" value={penetrate.data.top10_concentration * 100} precision={1} suffix="%" /></Card>
            <Card className="mini-stat-card"><Statistic title="HHI指数" value={penetrate.data.hhi} precision={4} /></Card>
          </div>

          <Row gutter={[16, 16]} style={{ marginBottom: 16 }}>
            <Col xs={24} xl={14}>
              <Card className="soft-card" title="底层股票暴露 Top 30" size="small">
                <Table<StockExposureItem> columns={stockColumns} dataSource={penetrate.data.stock_exposures} rowKey="stock_code" size="small" pagination={false} scroll={{ y: 400 }} />
              </Card>
            </Col>
            <Col xs={24} xl={10}>
              <Card className="soft-card" title="行业分布" size="small">
                <ReactECharts
                  style={{ height: 350 }}
                  option={{
                    tooltip: { trigger: 'item', formatter: '{b}: {d}%' },
                    series: [{
                      type: 'pie',
                      radius: ['30%', '70%'],
                      data: penetrate.data.industry_distribution.map((i) => ({ name: i.industry, value: Math.round(i.weight * 10000) / 100 })),
                      label: { formatter: '{b}\n{d}%', fontSize: 11 },
                    }],
                  }}
                />
              </Card>
            </Col>
          </Row>
        </>
      )}

      {similarity.data && similarity.data.pairs.length > 0 && (
        <Card className="soft-card" title="持仓相似度" style={{ marginBottom: 16 }}>
          {similarity.data.warning && <Alert type="warning" message={similarity.data.warning} showIcon style={{ marginBottom: 12 }} />}
          <Table<SimilarityItem> columns={simColumns} dataSource={similarity.data.pairs} rowKey={(r) => `${r.fund_a}-${r.fund_b}`} size="small" pagination={false} />
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 股票选基 Tab
// ---------------------------------------------------------------------------

function StockFundTab() {
  const [stockCode, setStockCode] = useState('');
  const [searchCode, setSearchCode] = useState('');
  const { data, isLoading } = useFundsByStock(searchCode);
  const navigate = useNavigate();

  const handleSearch = () => {
    if (stockCode.trim()) setSearchCode(stockCode.trim());
  };

  return (
    <div>
      <Card className="filter-card" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input
            placeholder="输入股票代码（如 600519、000858）"
            style={{ width: 300 }}
            value={stockCode}
            onChange={(e) => setStockCode(e.target.value)}
            onPressEnter={handleSearch}
          />
          <Button type="primary" icon={<StockOutlined />} onClick={handleSearch} loading={isLoading}>
            查找重仓基金
          </Button>
        </Space>
      </Card>

      {data && (
        <Card className="soft-card" title={`重仓 ${data.stock_name || data.stock_code} 的基金（共 ${data.total} 只）`}>
          <Table
            dataSource={data.funds}
            rowKey="fund_code"
            size="middle"
            pagination={{ pageSize: 20 }}
            onRow={(record) => ({ onClick: () => navigate(`/funds/${record.fund_code}`), style: { cursor: 'pointer' } })}
            columns={[
              { title: '基金代码', dataIndex: 'fund_code', key: 'fund_code', width: 100 },
              { title: '基金名称', dataIndex: 'fund_name', key: 'fund_name', ellipsis: true },
              { title: '持仓权重', dataIndex: 'weight', key: 'weight', width: 120, render: (v: number) => <Progress percent={Math.round(v * 100)} size="small" format={() => `${(v * 100).toFixed(2)}%`} /> },
              { title: '报告日期', dataIndex: 'report_date', key: 'report_date', width: 120 },
            ]}
          />
        </Card>
      )}

      {!data && !isLoading && searchCode && <Card className="soft-card"><Empty description="未找到持有该股票的基金" /></Card>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// 估值分析 Tab
// ---------------------------------------------------------------------------

function ValuationTab() {
  const [codes, setCodes] = useState('');
  const [fundCodes, setFundCodes] = useState<string[]>([]);
  const { data, isLoading } = useValuation(fundCodes);
  const navigate = useNavigate();

  const handleAnalyze = () => {
    const parsed = codes.split(/[,，\s]+/).filter(Boolean);
    if (parsed.length < 1) { message.warning('请输入至少1只基金代码'); return; }
    setFundCodes(parsed);
  };

  const zoneColors: Record<string, string> = { low: 'green', normal: 'blue', high: 'red' };
  const zoneLabels: Record<string, string> = { low: '历史低位', normal: '正常', high: '历史高位' };

  const columns: ColumnsType<ValuationItem> = [
    { title: '基金代码', dataIndex: 'fund_code', key: 'fund_code', width: 100, render: (code: string) => <a onClick={() => navigate(`/funds/${code}`)}>{code}</a> },
    { title: '当前净值', dataIndex: 'current_nav', key: 'current_nav', width: 100, render: (v: number) => v.toFixed(4) },
    { title: '历史百分位', dataIndex: 'percentile', key: 'percentile', width: 140, render: (v: number) => <Progress percent={Math.round(v * 100)} size="small" status={v > 0.7 ? 'exception' : v < 0.3 ? 'success' : 'normal'} /> },
    { title: '分位区间', dataIndex: 'zone', key: 'zone', width: 90, render: (zone: string) => <Tag color={zoneColors[zone]}>{zoneLabels[zone] || zone}</Tag> },
    { title: '建议', dataIndex: 'suggestion', key: 'suggestion', ellipsis: true },
    { title: '历史天数', dataIndex: 'history_days', key: 'history_days', width: 90 },
    { title: '历史最低', dataIndex: 'history_low', key: 'history_low', width: 90, render: (v: number) => v.toFixed(4) },
    { title: '历史最高', dataIndex: 'history_high', key: 'history_high', width: 90, render: (v: number) => v.toFixed(4) },
  ];

  return (
    <div>
      <Card className="filter-card" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Input
            placeholder="输入基金代码，多只用逗号分隔（适用于指数基金）"
            style={{ width: 500, maxWidth: '100%' }}
            value={codes}
            onChange={(e) => setCodes(e.target.value)}
            onPressEnter={handleAnalyze}
          />
          <Button type="primary" icon={<FundOutlined />} onClick={handleAnalyze} loading={isLoading}>
            净值分位分析
          </Button>
        </Space>
        <div style={{ marginTop: 8 }}>
          <Text type="secondary">提示：本分析基于净值历史百分位（非 PE/PB 估值），最适合指数基金。低于30%为历史低位区间，高于70%为历史高位区间。主动管理型基金净值高不代表“高估”，请结合其他指标判断。</Text>
        </div>
      </Card>

      {data && data.funds.length > 0 && (
        <Card className="soft-card" title="净值分位分析结果">
          <Table<ValuationItem> columns={columns} dataSource={data.funds} rowKey="fund_code" size="middle" pagination={false} />
        </Card>
      )}

      {data && data.funds.length === 0 && <Card className="soft-card"><Empty description="无法计算分位（数据不足60天）" /></Card>}
    </div>
  );
}
