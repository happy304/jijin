import { Alert, Button, Card, List, Space, Tag, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import type { AdvisorReminder } from '@/api/advisor';
import {
  reminderCategoryLabel,
  reminderTagColor,
  type AdvisorReminderCenterItem,
} from '@/components/AdvisorReminderCenter';

const { Text } = Typography;

export type AdvisorActiveReminderListItem = AdvisorReminder & { ui: AdvisorReminderCenterItem };

function reminderSeverityColor(level: AdvisorReminderCenterItem['level']): string {
  if (level === 'error') return 'red';
  if (level === 'warning') return 'orange';
  if (level === 'success') return 'green';
  return 'blue';
}

export function AdvisorActiveReminderListCard({
  items,
  loading,
  refreshing,
  onRefresh,
  onOpen,
  onDismiss,
}: {
  items: AdvisorActiveReminderListItem[];
  loading: boolean;
  refreshing: boolean;
  onRefresh: () => void;
  onOpen: (advisorResultId: number) => void;
  onDismiss: (reminderId: number) => void;
}) {
  return (
    <Card
      size="small"
      title="提醒列表"
      style={{ marginBottom: 16 }}
      extra={<Button size="small" icon={<ReloadOutlined />} loading={refreshing} onClick={onRefresh}>刷新提醒</Button>}
    >
      {loading ? (
        <Card loading size="small" variant="borderless" />
      ) : items.length === 0 ? (
        <Alert type="success" showIcon message="当前没有活跃提醒" description="每日 23:10 会自动刷新提醒；你也可以手动点击右上角刷新。" />
      ) : (
        <List
          size="small"
          dataSource={items.slice(0, 8)}
          renderItem={(item) => (
            <List.Item
              actions={[
                <Button key="open" size="small" type="link" onClick={() => onOpen(item.advisor_result_id)}>查看</Button>,
                <Button key="dismiss" size="small" type="link" onClick={() => onDismiss(item.id)}>忽略</Button>,
              ]}
            >
              <Space direction="vertical" size={2} style={{ width: '100%' }}>
                <Space wrap>
                  <Text strong>{item.title}</Text>
                  <Tag color={reminderTagColor(item.ui.category)}>{reminderCategoryLabel(item.ui.category)}</Tag>
                  <Tag color={reminderSeverityColor(item.ui.level)}>{item.severity}</Tag>
                  {item.fund_code ? <Tag>{item.fund_code}</Tag> : null}
                </Space>
                <Text type="secondary" style={{ fontSize: 12 }}>{item.description}</Text>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  检查记录 #{item.advisor_result_id}{item.trigger_date ? ` · 触发日 ${item.trigger_date}` : ''}
                </Text>
              </Space>
            </List.Item>
          )}
        />
      )}
    </Card>
  );
}
