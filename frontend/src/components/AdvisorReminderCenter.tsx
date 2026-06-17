import { Alert, Card, Checkbox, Space, Tag } from 'antd';

export type AdvisorReminderCategory = 'validity' | 'risk' | 'execution' | 'plan';

export interface AdvisorReminderCenterItem {
  key: string;
  level: 'info' | 'warning' | 'error' | 'success';
  category: AdvisorReminderCategory;
  title: string;
  description: string;
}

export function reminderTagColor(category: AdvisorReminderCategory): string {
  if (category === 'risk') return 'red';
  if (category === 'execution') return 'blue';
  if (category === 'validity') return 'orange';
  return 'purple';
}

export function reminderCategoryLabel(category: AdvisorReminderCategory): string {
  if (category === 'risk') return '风险';
  if (category === 'execution') return '执行';
  if (category === 'validity') return '时效';
  return '计划';
}

export function AdvisorReminderCenter({
  items,
  title = '提醒中心',
  enabledCategories,
  onChangeCategories,
}: {
  items: AdvisorReminderCenterItem[];
  title?: string;
  enabledCategories?: AdvisorReminderCategory[];
  onChangeCategories?: (categories: AdvisorReminderCategory[]) => void;
}) {
  const visibleItems = enabledCategories && enabledCategories.length > 0
    ? items.filter((item) => enabledCategories.includes(item.category))
    : items;

  return (
    <Space direction="vertical" size={8} style={{ width: '100%' }}>
      <Card
        size="small"
        type="inner"
        title={title}
        extra={onChangeCategories ? (
          <Checkbox.Group
            options={[
              { label: '时效', value: 'validity' },
              { label: '风险', value: 'risk' },
              { label: '执行', value: 'execution' },
              { label: '计划', value: 'plan' },
            ]}
            value={enabledCategories}
            onChange={(values) => onChangeCategories(values as AdvisorReminderCategory[])}
          />
        ) : null}
      >
        {visibleItems.length === 0 ? (
          <Alert type="success" showIcon message="当前筛选下没有需要特别处理的事项" />
        ) : (
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            {visibleItems.map((item) => (
              <Alert
                key={item.key}
                type={item.level}
                showIcon
                message={<Space wrap><span>{item.title}</span><Tag color={reminderTagColor(item.category)}>{reminderCategoryLabel(item.category)}</Tag></Space>}
                description={item.description}
              />
            ))}
          </Space>
        )}
      </Card>
    </Space>
  );
}
