import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  Card,
  Table,
  Button,
  Space,
  Tag,
  Spin,
  Alert,
  Empty,
  Modal,
  message,
  Tooltip,
} from 'antd';
import {
  ReloadOutlined,
  SwapOutlined,
  DeleteOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
  ClockCircleOutlined,
  LineChartOutlined,
  RocketOutlined,
  ExperimentOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  useBacktestList,
  useDeleteBacktest,
  useRerunBacktest,
  type BacktestResult,
} from '@/api/backtests';

function navWarningTooltip(record: BacktestResult): string {
  const messages: string[] = [];
  if (record.nav_data_stale) {
    messages.push(record.nav_data_stale.message || '底层 NAV 复权口径已有更新，建议重新运行。');
  }
  if (record.nav_quality_warning) {
    const affectedFunds = record.nav_quality_warning.funds
      ? Object.keys(record.nav_quality_warning.funds).join('、')
      : '';
    const qualityMessage = record.nav_quality_warning.message || '部分 NAV 数据存在口径混用或质量提示。';
    messages.push(affectedFunds ? `${qualityMessage} 受影响基金：${affectedFunds}` : qualityMessage);
  }
  return messages.join('；');
}

export function BacktestsPage() {
  const navigate = useNavigate();
  const { data: backtests, isLoading, isError, error } = useBacktestList();
  const deleteBacktest = useDeleteBacktest();
  const rerunBacktest = useRerunBacktest();
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [rerunningId, setRerunningId] = useState<number | null>(null);

  const handleCompare = () => {
    if (selectedRowKeys.length < 2) {
      message.warning('请至少选择 2 个已完成的回测进行对比');
      return;
    }
    if (selectedRowKeys.length > 5) {
      message.warning('最多支持 5 个回测同时对比');
      return;
    }
    const ids = selectedRowKeys.join(',');
    navigate(`/backtests/compare?ids=${ids}`);
  };

  const records = backtests || [];
  const doneCount = records.filter((item) => item.status === 'done').length;
  const activeCount = records.filter((item) => item.status === 'running' || item.status === 'pending').length;
  const failedCount = records.filter((item) => item.status === 'failed').length;

  const columns: ColumnsType<BacktestResult> = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 70,
    },
    {
      title: '策略名称',
      key: 'strategy_name',
      width: 140,
      render: (_: unknown, record: BacktestResult) => {
        return record.strategy_name || `策略 #${record.strategy_id}`;
      },
    },
    {
      title: '起始日期',
      dataIndex: 'start_date',
      key: 'start_date',
      width: 110,
    },
    {
      title: '结束日期',
      dataIndex: 'end_date',
      key: 'end_date',
      width: 110,
    },
    {
      title: '初始资金',
      dataIndex: 'initial_capital',
      key: 'initial_capital',
      width: 120,
      align: 'right',
      render: (v: string | number) =>
        v != null ? Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2 }) : '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => {
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
      },
    },
    {
      title: '总收益率',
      key: 'total_return',
      width: 110,
      align: 'right',
      render: (_: unknown, record: BacktestResult) => {
        const v = record.metrics?.total_return;
        if (v == null) return '-';
        const color = v >= 0 ? '#cf1322' : '#3f8600';
        return <span style={{ color }}>{(v * 100).toFixed(2)}%</span>;
      },
    },
    {
      title: 'Sharpe',
      key: 'sharpe',
      width: 90,
      align: 'right',
      render: (_: unknown, record: BacktestResult) => {
        const v = record.metrics?.sharpe;
        return v != null ? v.toFixed(3) : '-';
      },
    },
    {
      title: '数据提示',
      key: 'nav_warning',
      width: 150,
      render: (_: unknown, record: BacktestResult) => {
        if (!record.nav_data_stale && !record.nav_quality_warning) return '-';
        return (
          <Tooltip title={navWarningTooltip(record)}>
            <Space size={4} wrap>
              {record.nav_data_stale && <Tag color="orange">净值已更新</Tag>}
              {record.nav_quality_warning && <Tag color="gold">NAV质量</Tag>}
            </Space>
          </Tooltip>
        );
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 180,
      render: (_: unknown, record: BacktestResult) => {
        const rerunDisabled = record.status === 'pending' || record.status === 'running';
        const rerunLoading = rerunningId === record.id;

        return (
          <Space>
            <Button type="link" size="small" onClick={() => navigate(`/backtests/${record.id}`)}>
              详情
            </Button>
            <Button
              type="link"
              size="small"
              icon={<ReloadOutlined />}
              disabled={rerunDisabled}
              loading={rerunLoading}
              onClick={() => {
                Modal.confirm({
                  title: '确认更新',
                  content: '将重新运行该回测，并覆盖当前结果，是否继续？',
                  okText: '继续',
                  cancelText: '取消',
                  onOk: async () => {
                    setRerunningId(record.id);
                    try {
                      await rerunBacktest.mutateAsync(record.id);
                      message.success('回测已重新启动');
                    } finally {
                      setRerunningId(null);
                    }
                  },
                });
              }}
            >
              更新
            </Button>
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={() => {
                Modal.confirm({
                  title: '确认删除',
                  content: '删除后无法恢复，确定要删除此回测记录吗？',
                  okText: '确认删除',
                  cancelText: '取消',
                  okButtonProps: { danger: true },
                  onOk: async () => {
                    await deleteBacktest.mutateAsync(record.id);
                    message.success('回测已删除');
                  },
                });
              }}
            >
              删除
            </Button>
          </Space>
        );
      },
    },
  ];

  if (isLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 100 }}>
        <Spin size="large" tip="加载回测列表中..." />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="page-shell">
        <Alert
          type="error"
          message="回测列表加载失败"
          description={error instanceof Error ? error.message : '获取回测列表时发生错误'}
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
            <LineChartOutlined /> Backtest Lab
          </div>
          <h2>回测分析与策略验证</h2>
          <p>
            用历史数据验证研究思路的收益、回撤、稳定性和数据质量敏感性。
            历史回测不代表未来收益，所有结果都需要结合样本区间、交易成本和复权口径解读。
          </p>
          <div className="section-hero-actions">
            <Button
              type="primary"
              icon={<SwapOutlined />}
              onClick={handleCompare}
              disabled={selectedRowKeys.length < 2}
            >
              对比 {selectedRowKeys.length > 0 ? `(${selectedRowKeys.length})` : ''}
            </Button>
            <Link to="/strategies">
              <Button icon={<ExperimentOutlined />}>策略管理</Button>
            </Link>
            <Link to="/discovery">
              <Button icon={<RocketOutlined />}>发现基金</Button>
            </Link>
          </div>
        </div>
      </section>

      <div className="mini-stat-grid">
        <Card className="mini-stat-card">
          <div className="mini-stat-label">回测总数</div>
          <div className="mini-stat-value">{records.length}</div>
          <div className="mini-stat-meta">当前保存的历史回测记录</div>
        </Card>
        <Card className="mini-stat-card">
          <div className="mini-stat-label">已完成</div>
          <div className="mini-stat-value" style={{ color: '#1f9d68' }}>{doneCount}</div>
          <div className="mini-stat-meta">可进入详情或参与对比分析</div>
        </Card>
        <Card className="mini-stat-card">
          <div className="mini-stat-label">运行/等待</div>
          <div className="mini-stat-value" style={{ color: '#176bff' }}>{activeCount}</div>
          <div className="mini-stat-meta">运行中结果可能仍在更新</div>
        </Card>
        <Card className="mini-stat-card">
          <div className="mini-stat-label">失败</div>
          <div className="mini-stat-value" style={{ color: '#d84a4a' }}>{failedCount}</div>
          <div className="mini-stat-meta">建议查看详情或重新运行</div>
        </Card>
      </div>

      <Alert
        type="info"
        showIcon
        message="回测仅用于验证个人研究思路"
        description="历史回测不代表未来收益，结果依赖数据质量、样本区间、策略参数、交易成本和复权口径。若列表中出现 NAV 数据提示，建议先复核数据或重新运行回测后再解读。"
      />

      <Card className="soft-card">
        {records.length === 0 ? (
          <div className="empty-guide">
            <Empty description="暂无回测记录" />
            <Space wrap style={{ marginTop: 12 }}>
              <Link to="/strategies">
                <Button icon={<ExperimentOutlined />}>先配置策略</Button>
              </Link>
              <Link to="/discovery">
                <Button type="primary" icon={<RocketOutlined />}>先发现基金</Button>
              </Link>
            </Space>
          </div>
        ) : (
          <Table<BacktestResult>
            columns={columns}
            dataSource={records}
            rowKey="id"
            size="middle"
            scroll={{ x: 1050 }}
            rowSelection={{
              selectedRowKeys,
              onChange: (keys) => setSelectedRowKeys(keys),
              getCheckboxProps: (record) => ({
                disabled: record.status !== 'done',
              }),
            }}
            pagination={{
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 条`,
            }}
          />
        )}
      </Card>
    </div>
  );
}
