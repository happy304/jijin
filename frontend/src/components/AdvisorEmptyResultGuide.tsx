import { Card, Empty, Typography } from 'antd';

const { Text } = Typography;

export function AdvisorEmptyResultGuide({ fundTypeCount }: { fundTypeCount?: number | null }) {
  return (
    <Card>
      <Empty description="选择基金或策略，填写持仓，点击「生成组合检查」" />
      {fundTypeCount != null && (
        <div style={{ marginTop: 24, textAlign: 'center' }}>
          <Text type="secondary">
            引擎版本 v5 智能增强 · 自适应权重 · 动态阈值 · 信号共识加成 · 支持 {fundTypeCount || 7} 种基金类型
          </Text>
        </div>
      )}
    </Card>
  );
}
