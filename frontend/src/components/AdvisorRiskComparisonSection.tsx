import { Alert, Card, Col, Row, Space, Table, Tag, Typography } from 'antd';
import type { AdvisorAnalyzeResponse, RiskLevel, TradingAdviceItem } from '@/api/advisor';
import {
  adviceActionLabel,
  adviceStrengthLabel,
  adviceStrengthTagColor,
  formatCurrency,
  riskLevelLabel,
  riskLevelTagColor,
} from '@/utils/advisorDisplay';

const { Text } = Typography;

const ACTION_TAG_COLOR: Record<string, string> = {
  buy: 'red',
  sell: 'green',
  hold: 'default',
  watch: 'blue',
};

function summarizeReasons(reasons: string[] | undefined, maxCount = 3): string[] {
  return (reasons || []).map((item) => String(item || '').trim()).filter(Boolean).slice(0, maxCount);
}

export function AdvisorRiskComparisonSection({ comparison }: { comparison: AdvisorAnalyzeResponse['risk_comparison'] }) {
  if (!comparison) return null;

  const items = (Object.values(comparison) || []).filter(Boolean);
  if (items.length === 0) return null;

  return (
    <Card title="三档风险对比" size="small" style={{ marginBottom: 16 }}>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="同一基金池下，对比保守 / 稳健 / 进取三档风险偏好的动作差异"
        description="可先看每档增配/减配关注数量和参考调整金额，再展开查看具体基金动作变化。"
      />
      <Row gutter={12} style={{ marginBottom: 12 }}>
        {items.map((item) => (
          <Col span={8} key={item.risk_level}>
            <Card size="small" bordered>
              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                <Space>
                  <Tag color={riskLevelTagColor(item.risk_level)}>{riskLevelLabel(item.risk_level)}</Tag>
                  <Text type="secondary">{item.fund_count} 只基金</Text>
                </Space>
                <Space size={4} wrap>
                  <Tag color="red">可关注增配 {item.summary.buy_count}</Tag>
                  <Tag color="green">可关注减配 {item.summary.sell_count}</Tag>
                  <Tag>继续观察 {item.summary.hold_count}</Tag>
                  <Tag color="blue">观察 {item.summary.watch_count || 0}</Tag>
                </Space>
                <Text strong>增配参考额：{formatCurrency(item.summary.total_buy_amount)}</Text>
                <Text type="secondary">减配参考额：{formatCurrency(item.summary.total_sell_amount)}</Text>
              </Space>
            </Card>
          </Col>
        ))}
      </Row>
      <Table
        size="small"
        rowKey={(row) => `${row.risk_level}-${row.fund_code}`}
        pagination={false}
        scroll={{ x: 960 }}
        dataSource={items.flatMap((item) => item.advices.map((advice) => ({
          ...advice,
          risk_level: item.risk_level,
        })))}
        columns={[
          { title: '风险档', dataIndex: 'risk_level', width: 90, render: (value: string) => <Tag color={riskLevelTagColor(value)}>{riskLevelLabel(value)}</Tag> },
          { title: '基金', key: 'fund', width: 180, render: (_, row: TradingAdviceItem & { risk_level: RiskLevel }) => <div><Text strong>{row.fund_code}</Text><br /><Text type="secondary" style={{ fontSize: 12 }}>{row.fund_name || '-'}</Text></div> },
          { title: '检查结论', dataIndex: 'action', width: 90, render: (value: string) => <Tag color={ACTION_TAG_COLOR[value] || 'default'}>{adviceActionLabel(value)}</Tag> },
          { title: '强度', key: 'strength', width: 90, render: (_, row: TradingAdviceItem) => <Tag color={adviceStrengthTagColor(row.action, row.confidence, row.strength)}>{adviceStrengthLabel(row.action, row.confidence, row.strength)}</Tag> },
          { title: '置信度', dataIndex: 'confidence', width: 100, render: (value: number) => `${Math.round(value * 100)}%` },
          { title: '参考调整金额', dataIndex: 'suggested_amount', width: 120, align: 'right' as const, render: (value: number) => formatCurrency(value) },
          { title: '摘要理由', key: 'summary', render: (_, row: TradingAdviceItem) => row.reasoning?.summary || summarizeReasons(row.reasons, 2).join('；') || '-' },
        ]}
      />
    </Card>
  );
}
