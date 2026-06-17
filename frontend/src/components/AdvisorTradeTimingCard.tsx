import { Card, Descriptions, List, Tag, Typography } from 'antd';
import type { TradeTimingResponse } from '@/api/advisor';
import {
  formatDateWithWeekday,
  formatRequestTime,
  tradeIntentLabel,
} from '@/utils/advisorDisplay';

const { Text } = Typography;

export function AdvisorTradeTimingCard({ timing }: { timing: TradeTimingResponse }) {
  return (
    <Card size="small" title="申赎时间线" type="inner" style={{ marginBottom: 12 }}>
      <Descriptions column={3} size="small">
        <Descriptions.Item label="操作类型">
          <Tag color={timing.trade_intent === 'subscribe' ? 'red' : timing.trade_intent === 'redeem' ? 'green' : 'default'}>
            {tradeIntentLabel(timing.trade_intent)}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="截止状态">
          {typeof timing.is_after_cutoff === 'boolean'
            ? <Tag color={timing.is_after_cutoff ? 'orange' : 'green'}>{timing.is_after_cutoff ? '已过15:00' : '15:00前'}</Tag>
            : '-'}
        </Descriptions.Item>
        <Descriptions.Item label="当前时间">{formatRequestTime(timing.request_time)}</Descriptions.Item>
        <Descriptions.Item label="受理T日">{formatDateWithWeekday(timing.accepted_trade_date)}</Descriptions.Item>
        <Descriptions.Item label="净值日">{formatDateWithWeekday(timing.nav_date)}</Descriptions.Item>
        <Descriptions.Item label="预计确认">{formatDateWithWeekday(timing.expected_confirm_date)}</Descriptions.Item>
        <Descriptions.Item label="预计到账">{formatDateWithWeekday(timing.expected_settlement_date)}</Descriptions.Item>
        <Descriptions.Item label="份额/资金可用">{formatDateWithWeekday(timing.expected_available_date)}</Descriptions.Item>
        <Descriptions.Item label="日历来源">{timing.calendar_source || '-'}</Descriptions.Item>
      </Descriptions>
      {timing.rule_basis && <Text type="secondary" style={{ fontSize: 12 }}>{timing.rule_basis}</Text>}
      {timing.warnings && timing.warnings.length > 0 && (
        <List
          size="small"
          dataSource={timing.warnings}
          style={{ marginTop: 6 }}
          renderItem={(item) => <List.Item style={{ padding: '2px 0' }}><Text type="secondary" style={{ fontSize: 12 }}>• {item}</Text></List.Item>}
        />
      )}
    </Card>
  );
}
