import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Typography,
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
  ExperimentOutlined,
  DeleteOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
  ClockCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import {
  useSimulationList,
  useDeleteSimulation,
  useRerunSimulation,
  type SimulationStatus,
} from '@/api/simulations';

const { Title } = Typography;

const SIMULATION_RESEARCH_NOTICE = '模拟预测仅用于压力测试和情景观察，不代表未来收益或达成概率承诺。结果依赖历史样本、分布假设、参数和数据质量，请勿作为交易指令。';

const METHOD_LABELS: Record<string, string> = {
  gbm: 'GBM',
  bootstrap: 'Bootstrap',
  hybrid: 'Hybrid',
};

function navWarningTooltip(record: SimulationStatus): string {
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

export function SimulationsPage() {
  const navigate = useNavigate();
  const { data: simulations, isLoading, isError, error } = useSimulationList();
  const deleteSimulation = useDeleteSimulation();
  const rerunSimulation = useRerunSimulation();
  const [rerunningId, setRerunningId] = useState<number | null>(null);

  const columns: ColumnsType<SimulationStatus> = [
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
      render: (_: unknown, record: SimulationStatus) => {
        return record.strategy_name || `策略 #${record.strategy_id}`;
      },
    },
    {
      title: '方法',
      dataIndex: 'method',
      key: 'method',
      width: 100,
      render: (method: string) => (
        <Tag color="blue">{METHOD_LABELS[method] || method}</Tag>
      ),
    },
    {
      title: '预测期限',
      dataIndex: 'horizon_days',
      key: 'horizon_days',
      width: 100,
      align: 'right',
      render: (days: number) => `${days} 天`,
    },
    {
      title: '模拟次数',
      dataIndex: 'num_simulations',
      key: 'num_simulations',
      width: 100,
      align: 'right',
      render: (n: number) => n?.toLocaleString(),
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
      title: '预期收益',
      key: 'expected_return',
      width: 110,
      align: 'right',
      render: (_: unknown, record: SimulationStatus) => {
        const v = record.metrics?.expected_return;
        if (v == null) return '-';
        const pct = (v * 100).toFixed(2);
        const color = v >= 0 ? '#cf1322' : '#3f8600';
        return <span style={{ color }}>{pct}%</span>;
      },
    },
    {
      title: 'VaR(95%)',
      key: 'var_95',
      width: 100,
      align: 'right',
      render: (_: unknown, record: SimulationStatus) => {
        const v = record.metrics?.var?.['95'];
        if (v == null) return '-';
        return <span style={{ color: '#cf1322' }}>{(v * 100).toFixed(2)}%</span>;
      },
    },
    {
      title: '达成概率',
      key: 'target_prob',
      width: 100,
      align: 'right',
      render: (_: unknown, record: SimulationStatus) => {
        const v = record.metrics?.target_probability;
        if (v == null) return '-';
        return `${(v * 100).toFixed(1)}%`;
      },
    },
    {
      title: '数据提示',
      key: 'nav_warning',
      width: 150,
      render: (_: unknown, record: SimulationStatus) => {
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
      width: 190,
      render: (_: unknown, record: SimulationStatus) => {
        const rerunDisabled = record.status === 'pending' || record.status === 'running';
        const rerunLoading = rerunningId === record.id;

        return (
          <Space>
            <Button type="link" size="small" onClick={() => navigate(`/simulations/${record.id}`)}>
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
                  content: '将重新运行该模拟，并覆盖当前结果，是否继续？',
                  okText: '继续',
                  cancelText: '取消',
                  onOk: async () => {
                    setRerunningId(record.id);
                    try {
                      await rerunSimulation.mutateAsync(record.id);
                      message.success('模拟任务已重新启动');
                    } catch {
                      // 409 通常表示任务已经被提交为 pending/running；全局拦截器已提示。
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
                  content: '删除后无法恢复，确定要删除此模拟记录吗？',
                  okText: '确认删除',
                  cancelText: '取消',
                  okButtonProps: { danger: true },
                  onOk: async () => {
                    await deleteSimulation.mutateAsync(record.id);
                    message.success('模拟记录已删除');
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
        <Spin size="large" tip="加载模拟列表中..." />
      </div>
    );
  }

  if (isError) {
    return (
      <div style={{ padding: 24 }}>
        <Title level={3}>模拟预测</Title>
        <Alert
          type="error"
          message="加载失败"
          description={error instanceof Error ? error.message : '获取模拟列表时发生错误'}
          showIcon
        />
      </div>
    );
  }

  return (
    <div>
      <Space style={{ marginBottom: 16, width: '100%', justifyContent: 'space-between' }}>
        <Title level={3} style={{ margin: 0 }}>
          <ExperimentOutlined /> 模拟预测
        </Title>
      </Space>

      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="研究层情景模拟"
        description={SIMULATION_RESEARCH_NOTICE}
      />

      <Card>
        {!simulations || simulations.length === 0 ? (
          <Empty description="暂无模拟记录，请在策略详情页发起模拟预测" />
        ) : (
          <Table<SimulationStatus>
            columns={columns}
            dataSource={simulations}
            rowKey="id"
            size="middle"
            scroll={{ x: 1150 }}
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
