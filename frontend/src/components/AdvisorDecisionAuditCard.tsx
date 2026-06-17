import { Alert, Card, Descriptions, Divider, List, Space, Table, Tag, Typography } from 'antd';
import type { TradingAdviceItem } from '@/api/advisor';
import {
  formatAuditValue,
  formatMaybePct,
  formatPct,
  signalSourceLabel,
} from '@/utils/advisorDisplay';

const { Text } = Typography;

export function AdvisorDecisionAuditCard({ advice }: { advice: TradingAdviceItem }) {
  if (!advice.decision_audit) return null;

  return (
    <Card size="small" title="决策审计" type="inner" style={{ marginBottom: 12 }}>
      <Descriptions column={3} size="small">
        <Descriptions.Item label="增配关注阈值">{advice.decision_audit.effective_buy_threshold.toFixed(2)}</Descriptions.Item>
        <Descriptions.Item label="减配关注阈值">{advice.decision_audit.effective_sell_threshold.toFixed(2)}</Descriptions.Item>
        <Descriptions.Item label="阈值状态">
          <Tag color={advice.decision_audit.threshold_state === 'within_hold_band' ? 'default' : 'blue'}>
            {advice.decision_audit.threshold_state === 'above_buy_threshold' ? '超过增配关注阈值' : advice.decision_audit.threshold_state === 'below_sell_threshold' ? '跌破减配关注阈值' : '处于观察区间'}
          </Tag>
        </Descriptions.Item>
        <Descriptions.Item label="距阈值差">{(advice.decision_audit.threshold_margin * 100).toFixed(1)}</Descriptions.Item>
        <Descriptions.Item label="缺失信号源">{advice.decision_audit.missing_sources} 个</Descriptions.Item>
        <Descriptions.Item label="净值样本">{formatAuditValue(advice.decision_audit.data_quality.nav_count)}</Descriptions.Item>
        <Descriptions.Item label="数据区间" span={2}>{formatAuditValue(advice.decision_audit.data_quality.data_start)} → {formatAuditValue(advice.decision_audit.data_quality.data_end)}</Descriptions.Item>
        <Descriptions.Item label="样本充足">{formatAuditValue(advice.decision_audit.data_quality.sample_sufficient)}</Descriptions.Item>
        <Descriptions.Item label="预测样本">{formatAuditValue(advice.decision_audit.data_quality.prediction_sample_size)}</Descriptions.Item>
        <Descriptions.Item label="当前波动率">{formatMaybePct(advice.decision_audit.data_quality.current_volatility)}</Descriptions.Item>
        <Descriptions.Item label="波动率分位">{formatMaybePct(advice.decision_audit.data_quality.volatility_percentile)}</Descriptions.Item>
      </Descriptions>
      <Divider style={{ margin: '8px 0' }} />
      {advice.decision_audit.signal_contributions && advice.decision_audit.signal_contributions.length > 0 ? (
        <Table
          size="small"
          pagination={false}
          rowKey="source"
          dataSource={advice.decision_audit.signal_contributions}
          columns={[
            { title: '信号源', dataIndex: 'source', render: (v: string) => signalSourceLabel(v) },
            { title: '分数', dataIndex: 'score', align: 'right' as const, render: (v: number) => (v * 100).toFixed(1) },
            { title: '权重', dataIndex: 'weight', align: 'right' as const, render: (v: number) => formatPct(v) },
            { title: '贡献', dataIndex: 'contribution', align: 'right' as const, render: (v: number) => <Text style={{ color: v > 0 ? '#cf1322' : v < 0 ? '#3f8600' : undefined }}>{(v * 100).toFixed(1)}</Text> },
            { title: '状态', dataIndex: 'available', align: 'center' as const, render: (v: boolean) => <Tag color={v ? 'blue' : 'default'}>{v ? '可用' : '不可用'}</Tag> },
          ]}
        />
      ) : (
        <Space size={4} wrap>
          {Object.entries(advice.decision_audit.signal_weights).map(([key, weight]) => (
            <Tag key={key} color={(advice.decision_audit?.signal_availability[key] ?? true) ? 'blue' : 'default'}>
              {key}: {(weight * 100).toFixed(0)}%
            </Tag>
          ))}
          {Object.entries(advice.decision_audit.signal_availability).filter(([, ok]) => !ok).map(([key]) => (
            <Tag key={`missing-${key}`} color="orange">{key} 不可用</Tag>
          ))}
        </Space>
      )}
      {advice.decision_audit.dominant_signal?.single_signal_dominant && (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 8 }}
          message={`最终检查结论主要依赖 ${signalSourceLabel(advice.decision_audit.dominant_signal.source)} 信号`}
          description={`该信号贡献占比 ${formatPct(advice.decision_audit.dominant_signal.contribution_share)}，需注意单一信号主导风险。`}
        />
      )}
      {advice.decision_audit.market_regime && (
        <div style={{ marginTop: 8 }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            市场状态：{formatAuditValue(advice.decision_audit.market_regime.regime)}；趋势：{formatAuditValue(advice.decision_audit.market_regime.trend_state)}；波动：{formatAuditValue(advice.decision_audit.market_regime.volatility_state)}；权重乘数：{formatAuditValue(advice.decision_audit.market_regime.signal_weight_multiplier)}
          </Text>
        </div>
      )}
      {advice.decision_audit.notes.length > 0 && (
        <List size="small" dataSource={advice.decision_audit.notes} renderItem={(item)=><List.Item style={{ padding: '2px 0' }}><Text type="secondary" style={{ fontSize: 12 }}>• {item}</Text></List.Item>} />
      )}
    </Card>
  );
}
