import { Alert, Card, Col, Row, Space, Statistic, Table, Tag, Typography } from 'antd';
import { useAdvicePerformance } from '@/api/advisor';
import {
  driftLevelColor,
  driftLevelLabel,
  evaluationLabelColor,
  evaluationLabelText,
  executionStatusColor,
  executionStatusLabel,
  executionSummaryStatusLabel,
} from '@/utils/advisorDisplay';

const { Text } = Typography;

const ACTION_CONFIG = {
  buy: { text: '可关注增配', tagColor: 'red' },
  sell: { text: '可关注减配', tagColor: 'green' },
  hold: { text: '继续观察', tagColor: 'default' },
  watch: { text: '观察', tagColor: 'blue' },
};

export function AdvisorPerformanceCard({ resultId }: { resultId: number }) {
  const { data: perf, isLoading } = useAdvicePerformance(resultId);

  if (isLoading) return <Card size="small" loading style={{ marginBottom: 16 }} />;
  if (!perf) return null;

  if (perf.status === 'pending') {
    return (
      <Alert
        type="info"
        showIcon
        message="执行效果跟踪中"
        description={perf.message || '等待每日 23:00 跟踪任务运行后可查看实际表现'}
        style={{ marginBottom: 16 }}
      />
    );
  }

  const { summary, tracked_returns } = perf;
  if (!summary || !tracked_returns) return null;
  const executionSummary = perf.execution_summary || summary.execution_summary;

  return (
    <Card size="small" title="检查结果跟踪效果（实际表现）" style={{ marginBottom: 16 }}>
      {executionSummary && (
        <Alert
          type="info"
          showIcon
          message={`执行归因：${executionSummaryStatusLabel(executionSummary.status)}`}
          description={`${executionSummary.interpretation} 下表收益为模型参考结果后的市场表现，执行状态用于判断用户是否采纳及金额偏离。`}
          style={{ marginBottom: 12 }}
        />
      )}
      <Row gutter={16} style={{ marginBottom: 12 }}>
        <Col span={6}>
          <Statistic
            title="增配关注命中率(20日)"
            value={summary.buy_hit_rate_20d != null ? `${(summary.buy_hit_rate_20d * 100).toFixed(1)}%` : '-'}
            suffix={summary.buy_count > 0 ? `(${summary.buy_count}只)` : ''}
            valueStyle={{ color: (summary.buy_hit_rate_20d ?? 0) > 0.5 ? '#3f8600' : '#cf1322' }}
          />
        </Col>
        <Col span={6}>
          <Statistic
            title="减配关注命中率(20日)"
            value={summary.sell_hit_rate_20d != null ? `${(summary.sell_hit_rate_20d * 100).toFixed(1)}%` : '-'}
            suffix={summary.sell_count > 0 ? `(${summary.sell_count}只)` : ''}
            valueStyle={{ color: (summary.sell_hit_rate_20d ?? 0) > 0.5 ? '#3f8600' : '#cf1322' }}
          />
        </Col>
        <Col span={6}>
          <Statistic
            title="增配关注平均收益(20日)"
            value={summary.buy_avg_return_20d != null ? `${(summary.buy_avg_return_20d * 100).toFixed(2)}%` : '-'}
            valueStyle={{ color: (summary.buy_avg_return_20d ?? 0) > 0 ? '#3f8600' : '#cf1322' }}
          />
        </Col>
        <Col span={6}>
          <Statistic title="跟踪基金数" value={summary.total_tracked} />
        </Col>
      </Row>
      {summary.evaluation_labels && (
        <Space wrap style={{ marginBottom: 12 }}>
          <Tag color="green">有效 {summary.evaluation_labels.effective}</Tag>
          <Tag color="blue">中性 {summary.evaluation_labels.neutral}</Tag>
          <Tag color="red">失效 {summary.evaluation_labels.ineffective}</Tag>
          <Tag>暂不可评估 {summary.evaluation_labels.not_evaluable}</Tag>
        </Space>
      )}

      <Table
        size="small"
        dataSource={Object.entries(tracked_returns).map(([code, data]) => ({ code, ...data }))}
        rowKey="code"
        pagination={false}
        columns={[
          { title: '基金', dataIndex: 'code', width: 80 },
          { title: '检查结论', dataIndex: 'action', width: 60, render: (a: string) => { const c = ACTION_CONFIG[a as keyof typeof ACTION_CONFIG] || ACTION_CONFIG.hold; return <Tag color={c.tagColor}>{c.text}</Tag>; } },
          { title: '5日', dataIndex: 'return_5d', width: 70, render: (v: number | null) => v != null ? <Text style={{ color: v > 0 ? '#3f8600' : '#cf1322' }}>{(v * 100).toFixed(2)}%</Text> : '-' },
          { title: '10日', dataIndex: 'return_10d', width: 70, render: (v: number | null) => v != null ? <Text style={{ color: v > 0 ? '#3f8600' : '#cf1322' }}>{(v * 100).toFixed(2)}%</Text> : '-' },
          { title: '20日', dataIndex: 'return_20d', width: 70, render: (v: number | null) => v != null ? <Text style={{ color: v > 0 ? '#3f8600' : '#cf1322' }}>{(v * 100).toFixed(2)}%</Text> : '-' },
          { title: '60日', dataIndex: 'return_60d', width: 70, render: (v: number | null) => v != null ? <Text style={{ color: v > 0 ? '#3f8600' : '#cf1322' }}>{(v * 100).toFixed(2)}%</Text> : '-' },
          { title: '20日命中', dataIndex: 'hit_20d', width: 70, render: (v: boolean | null) => v === true ? <Tag color="green">✓</Tag> : v === false ? <Tag color="red">✗</Tag> : '-' },
          { title: '效果标签', dataIndex: 'evaluation_label', width: 100, render: (value: string | null | undefined) => <Tag color={evaluationLabelColor(value)}>{evaluationLabelText(value)}</Tag> },
          { title: '执行状态', key: 'execution_status', width: 100, render: (_, row) => <Tag color={executionStatusColor(row.execution_attribution?.latest_status)}>{executionStatusLabel(row.execution_attribution?.latest_status)}</Tag> },
          { title: '采纳', key: 'adopted', width: 70, render: (_, row) => row.execution_attribution?.adopted ? <Tag color="green">是</Tag> : <Tag>否</Tag> },
          { title: '金额执行率', key: 'execution_ratio', width: 100, render: (_, row) => row.execution_attribution?.amount_execution_ratio != null ? `${(row.execution_attribution.amount_execution_ratio * 100).toFixed(0)}%` : '-' },
          { title: '执行偏离', key: 'drift_level', width: 110, render: (_, row) => <Tag color={driftLevelColor(row.execution_attribution?.drift_level)}>{driftLevelLabel(row.execution_attribution?.drift_level)}</Tag> },
        ]}
      />
      {perf.tracked_at && <Text type="secondary" style={{ fontSize: 11, marginTop: 8, display: 'block' }}>跟踪更新时间: {new Date(perf.tracked_at).toLocaleString('zh-CN')}</Text>}
    </Card>
  );
}
