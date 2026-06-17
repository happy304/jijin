import { Card, List, Space, Tag, Typography } from 'antd';
import type { ExecutionPlanTaskItem as ApiExecutionPlanTaskItem } from '@/api/advisor';
import {
  executionStatusColor,
  executionStatusLabel,
  formatCurrency,
  formatDateWithWeekday,
} from '@/utils/advisorDisplay';

const { Text } = Typography;

type ExecutionPlanTaskItem = ApiExecutionPlanTaskItem & { key?: string };

function daysUntilDate(value: string | null | undefined): number | null {
  if (!value) return null;
  const target = new Date(`${value}T23:59:59+08:00`);
  if (Number.isNaN(target.getTime())) return null;
  const now = new Date();
  const diff = target.getTime() - now.getTime();
  return Math.ceil(diff / (24 * 60 * 60 * 1000));
}

function renderTaskStatusTag(task: ExecutionPlanTaskItem) {
  const days = daysUntilDate(task.scheduled_date);
  if (task.status === 'done') return <Tag color="green">已完成</Tag>;
  if (task.status === 'skipped') return <Tag color="default">已跳过</Tag>;
  if (days != null && days < 0) return <Tag color="red">已逾期</Tag>;
  if (days === 0) return <Tag color="orange">今天</Tag>;
  if (days != null && days <= 3) return <Tag color="blue">近期</Tag>;
  return <Tag>待执行</Tag>;
}

export function AdvisorExecutionPlanTaskList({ tasks }: { tasks: ExecutionPlanTaskItem[] }) {
  if (tasks.length === 0) return null;

  return (
    <Card size="small" type="inner" title="未来待执行任务" style={{ marginTop: 8 }}>
      <List
        size="small"
        dataSource={tasks}
        renderItem={(task) => (
          <List.Item style={{ padding: '8px 0' }}>
            <Space direction="vertical" size={2} style={{ width: '100%' }}>
              <Space wrap>
                <Text strong>{task.title}</Text>
                {renderTaskStatusTag(task)}
                <Tag>{formatDateWithWeekday(task.scheduled_date)}</Tag>
                <Tag color="purple">{formatCurrency(task.amount_min)} - {formatCurrency(task.amount_max)}</Tag>
                {task.matched_execution_status ? <Tag color={executionStatusColor(task.matched_execution_status)}>{executionStatusLabel(task.matched_execution_status)}</Tag> : null}
              </Space>
              <Text style={{ fontSize: 12 }}>{task.description}</Text>
              {task.trigger_summary ? <Text type="secondary" style={{ fontSize: 12 }}>触发说明：{task.trigger_summary}</Text> : null}
              {task.matched_executed_date ? <Text type="secondary" style={{ fontSize: 12 }}>最近执行日期：{formatDateWithWeekday(task.matched_executed_date)}</Text> : null}
            </Space>
          </List.Item>
        )}
      />
    </Card>
  );
}
