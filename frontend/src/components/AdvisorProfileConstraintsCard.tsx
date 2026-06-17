import { Card, List, Space, Tag, Typography } from 'antd';
import type { TradingAdviceItem } from '@/api/advisor';

const { Text } = Typography;

export function AdvisorProfileConstraintsCard({ advice }: { advice: TradingAdviceItem }) {
  if (!advice.profile_constraints || advice.profile_constraints.length === 0) return null;

  return (
    <Card size="small" title="投资画像约束" type="inner" style={{ marginBottom: 12 }}>
      <List
        size="small"
        dataSource={advice.profile_constraints.filter((item) => item.triggered)}
        renderItem={(item) => (
          <List.Item style={{ padding: '4px 0' }}>
            <Space>
              <Tag color={item.effect === 'reduce_amount' ? 'orange' : item.effect === 'hold' ? 'red' : 'blue'}>{item.name}</Tag>
              <Text style={{ fontSize: 12 }}>{item.explanation}</Text>
            </Space>
          </List.Item>
        )}
      />
    </Card>
  );
}
