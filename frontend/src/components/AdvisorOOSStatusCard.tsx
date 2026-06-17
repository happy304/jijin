import { Alert, Button, Card, Col, Row, Space, Statistic, Table, Typography, message } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import type { ColumnsType } from 'antd/es/table';
import { useQueryClient } from '@tanstack/react-query';
import { useOOSStatus, useTriggerOOSRefresh, type OOSCoverageItem } from '@/api/advisor';

const { Text } = Typography;

const riskLabelMap: Record<string, string> = {
  conservative: '保守型',
  moderate: '稳健型',
  aggressive: '进取型',
};

export function AdvisorOOSStatusCard() {
  const { data: status, isLoading } = useOOSStatus();
  const queryClient = useQueryClient();
  const triggerRefreshMutation = useTriggerOOSRefresh();

  if (isLoading) return <Card loading style={{ marginBottom: 16 }} />;
  if (!status) return null;

  const columns: ColumnsType<{ key: string } & OOSCoverageItem> = [
    { title: '风险档', dataIndex: 'key', width: 90, render: (value: string) => riskLabelMap[value] || value },
    { title: '精确命中', dataIndex: 'exact_count', width: 90 },
    { title: '可解析覆盖', dataIndex: 'resolved_count', width: 100 },
    { title: '精确覆盖率', dataIndex: 'exact_coverage_pct', width: 100, render: (v: number | null) => v != null ? `${(v * 100).toFixed(0)}%` : '-' },
    { title: '总覆盖率', dataIndex: 'resolved_coverage_pct', width: 100, render: (v: number | null) => v != null ? `${(v * 100).toFixed(0)}%` : '-' },
    { title: '回退到稳健档', dataIndex: 'fallback_to_moderate', width: 110 },
    { title: '回退到最近缓存', dataIndex: 'fallback_to_latest', width: 120 },
    { title: '过期缓存', dataIndex: 'stale_count', width: 90 },
    { title: '缺失', dataIndex: 'missing_count', width: 80 },
  ];

  const dataSource = Object.entries(status.coverage).map(([key, value]) => ({ key, ...value }));

  const handleTriggerRefresh = async () => {
    try {
      const result = await triggerRefreshMutation.mutateAsync();
      message.success(`${result.message}（任务 ${result.task_id.slice(0, 8)}）`);
      queryClient.invalidateQueries({ queryKey: ['advisor-oos-status'] });
    } catch {
      message.error('提交 OOS 刷新任务失败');
    }
  };

  return (
    <Card
      title="🗂 OOS 缓存状态"
      style={{ marginBottom: 16 }}
      extra={
        <Space>
          {status.latest_snapshot_update ? <Text type="secondary" style={{ fontSize: 12 }}>最新缓存: {status.latest_snapshot_update}</Text> : null}
          <Button size="small" icon={<ReloadOutlined />} loading={triggerRefreshMutation.isPending} onClick={handleTriggerRefresh}>
            立即触发 nightly 刷新
          </Button>
        </Space>
      }
    >
      <Row gutter={16} style={{ marginBottom: 12 }}>
        <Col span={6}><Statistic title="活跃基金池" value={status.total_active_funds} /></Col>
        <Col span={6}><Statistic title="nightly 风险档" value={riskLabelMap[status.nightly_refresh.risk_level] || status.nightly_refresh.risk_level} /></Col>
        <Col span={6}><Statistic title="错峰批大小" value={status.nightly_refresh.dispatch_every_n} suffix="只/批" /></Col>
        <Col span={6}><Statistic title="批间延迟" value={status.nightly_refresh.dispatch_countdown_step} suffix="秒" /></Col>
      </Row>
      <Alert
        type="info"
        showIcon
        message={`nightly 刷新：${status.nightly_refresh.schedule}`}
        description={`默认刷新 ${riskLabelMap[status.nightly_refresh.risk_level] || status.nightly_refresh.risk_level} 风险档；缓存超过 ${status.nightly_refresh.max_age_days} 天会被重新派发 Walk-Forward 子任务。当前配置：${status.nightly_refresh.max_funds} 只基金上限、每 ${status.nightly_refresh.dispatch_every_n} 只为一批、批间延迟 ${status.nightly_refresh.dispatch_countdown_step} 秒。`}
        style={{ marginBottom: 12 }}
      />
      <Table
        size="small"
        rowKey="key"
        dataSource={dataSource}
        columns={columns}
        pagination={false}
        scroll={{ x: 900 }}
      />
      {status.fund_codes_sample.length > 0 && (
        <Text type="secondary" style={{ fontSize: 11, marginTop: 8, display: 'block' }}>
          当前基金池示例：{status.fund_codes_sample.join(', ')}
        </Text>
      )}
    </Card>
  );
}
