import type { ReactNode } from 'react';
import { Card, Col, Row, Space, Statistic, Tag, Typography } from 'antd';
import type { AdvisorHistoryDetailResponse } from '@/api/advisor';

const { Text } = Typography;

function historyRiskLevelLabel(level: string | null | undefined): string {
  if (level === 'conservative') return '保守型';
  if (level === 'aggressive') return '进取型';
  return '稳健型';
}

function formatHistoryUpdateTime(detail: AdvisorHistoryDetailResponse): string {
  const value = detail.updated_at || detail.created_at || '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString('zh-CN');
}

export function AdvisorHistoryDetailSummaryCard({
  detail,
  children,
}: {
  detail: AdvisorHistoryDetailResponse;
  children: ReactNode;
}) {
  return (
    <Card
      title={`检查详情 — ${detail.advice_date}`}
      extra={(
        <Space>
          <Tag color="blue">{historyRiskLevelLabel(detail.risk_level)}</Tag>
          <Text type="secondary">资金: ¥{detail.total_capital.toLocaleString()}</Text>
          <Text type="secondary">更新时间: {formatHistoryUpdateTime(detail)}</Text>
          {detail.strategy_name && <Tag>{detail.strategy_name}</Tag>}
        </Space>
      )}
      style={{ marginBottom: 16 }}
    >
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={4}><Statistic title="可关注增配" value={detail.summary.buy_count} suffix="只" valueStyle={{ color: '#cf1322' }} /></Col>
        <Col span={4}><Statistic title="可关注减配" value={detail.summary.sell_count} suffix="只" valueStyle={{ color: '#3f8600' }} /></Col>
        <Col span={4}><Statistic title="继续观察" value={detail.summary.hold_count} suffix="只" /></Col>
        <Col span={4}><Statistic title="观察" value={detail.summary.watch_count || 0} suffix="只" valueStyle={{ color: '#1677ff' }} /></Col>
        <Col span={4}><Statistic title="增配参考额" value={detail.summary.total_buy_amount} prefix="¥" precision={0} /></Col>
        <Col span={4}><Statistic title="基金数" value={detail.fund_codes.length} suffix="只" /></Col>
      </Row>
      {children}
    </Card>
  );
}
