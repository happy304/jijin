import { Alert, Card, Col, Descriptions, List, Row, Space, Tag, Typography } from 'antd';
import type { TradingAdviceItem, ExecutionPlanTaskItem as ApiExecutionPlanTaskItem } from '@/api/advisor';
import { AdvisorExecutionPlanTaskList as ExecutionPlanTaskList } from '@/components/AdvisorExecutionPlanTaskList';
import {
  formatCurrency,
  formatPct,
} from '@/utils/advisorDisplay';

const { Text } = Typography;

type ExecutionPlanTaskItem = ApiExecutionPlanTaskItem & { key: string };

const executionTypeLabel = {
  one_time: '一次性参考',
  batch: '分批参考',
  fixed_investment: '定投参考',
  hold: '暂不操作',
} as const;

function tradePlanTriggerColor(severity: string | null | undefined): string {
  if (severity === 'high') return 'red';
  if (severity === 'warning') return 'orange';
  return 'blue';
}

function tradePlanTriggerLabel(triggerType: string | null | undefined): string {
  const map: Record<string, string> = {
    pause_buy: '暂停加仓',
    stop_buy: '停止加仓',
    reduce_position: '控制减仓',
    review: '复核条件',
    refresh: '刷新检查结果',
  };
  return map[triggerType || ''] || String(triggerType || '-');
}

function collectExecutionNotes(advice: TradingAdviceItem): string[] {
  const notes = [
    ...(advice.execution_notes || []),
    advice.trade_plan?.explanation || '',
    ...(advice.validity?.invalidation_rules || []),
    ...(advice.risk_warnings || []),
  ].map((item) => String(item || '').trim()).filter(Boolean);
  return Array.from(new Set(notes)).slice(0, 6);
}

export function AdvisorTradePlanImpactSection({
  advice,
  executionPlanTasks,
}: {
  advice: TradingAdviceItem;
  executionPlanTasks: ExecutionPlanTaskItem[];
}) {
  if (!advice.trade_plan && !advice.portfolio_impact) return null;

  return (
    <Row gutter={12} style={{ marginBottom: 12 }}>
      {advice.trade_plan && (
        <Col span={12}>
          <Card size="small" title="参考计划" type="inner">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="参考方式">{executionTypeLabel[advice.trade_plan.execution_type]}</Descriptions.Item>
              <Descriptions.Item label="参考调整金额">¥{advice.trade_plan.suggested_amount.toLocaleString()}</Descriptions.Item>
              <Descriptions.Item label="金额区间">{formatCurrency(advice.trade_amount_min ?? advice.trade_plan.min_amount)} - {formatCurrency(advice.trade_amount_max ?? advice.trade_plan.max_amount)}</Descriptions.Item>
              <Descriptions.Item label="当前/参考仓位区间">{formatPct(advice.trade_plan.current_weight)} → {formatPct(advice.trade_plan.target_weight)}</Descriptions.Item>
              {advice.trade_plan.batch_count && <Descriptions.Item label="分批计划">{advice.trade_plan.batch_count} 次，每 {advice.trade_plan.batch_interval_days || 7} 天左右</Descriptions.Item>}
            </Descriptions>
            <Text type="secondary" style={{ fontSize: 12 }}>{advice.trade_plan.explanation}</Text>
            {collectExecutionNotes(advice).length > 0 && (
              <List
                size="small"
                style={{ marginTop: 8 }}
                dataSource={collectExecutionNotes(advice)}
                renderItem={(item) => <List.Item style={{ padding: '2px 0' }}><Text type="secondary" style={{ fontSize: 12 }}>• {item}</Text></List.Item>}
              />
            )}
            {advice.trade_plan?.triggers && advice.trade_plan.triggers.length > 0 && (
              <Card size="small" type="inner" title="条件触发规则" style={{ marginTop: 8 }}>
                <List
                  size="small"
                  dataSource={advice.trade_plan.triggers}
                  renderItem={(trigger) => (
                    <List.Item style={{ padding: '6px 0' }}>
                      <Space direction="vertical" size={2} style={{ width: '100%' }}>
                        <Space wrap>
                          <Tag color={tradePlanTriggerColor(trigger.severity)}>{tradePlanTriggerLabel(trigger.trigger_type)}</Tag>
                          <Text strong style={{ fontSize: 12 }}>{trigger.condition}</Text>
                        </Space>
                        <Text style={{ fontSize: 12 }}>触发后动作：{trigger.action}</Text>
                        <Text type="secondary" style={{ fontSize: 12 }}>{trigger.reason}</Text>
                      </Space>
                    </List.Item>
                  )}
                />
              </Card>
            )}
            <ExecutionPlanTaskList tasks={executionPlanTasks} />
          </Card>
        </Col>
      )}
      {advice.portfolio_impact && (
        <Col span={12}>
          <Card size="small" title="组合影响" type="inner">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="本基金仓位">{formatPct(advice.portfolio_impact.before_weight)} → {formatPct(advice.portfolio_impact.after_weight)}</Descriptions.Item>
              <Descriptions.Item label="仓位变化">{formatPct(advice.portfolio_impact.position_change)}</Descriptions.Item>
              <Descriptions.Item label="风险变化">
                <Tag color={advice.portfolio_impact.risk_change === 'increase' ? 'orange' : advice.portfolio_impact.risk_change === 'decrease' ? 'green' : 'default'}>
                  {advice.portfolio_impact.risk_change === 'increase' ? '上升' : advice.portfolio_impact.risk_change === 'decrease' ? '下降' : '基本不变'}
                </Tag>
              </Descriptions.Item>
            </Descriptions>
            <Text type="secondary" style={{ fontSize: 12 }}>{advice.portfolio_impact.explanation}</Text>
            {advice.portfolio_impact.concentration_warning && <Alert type="warning" message={advice.portfolio_impact.concentration_warning} showIcon style={{ marginTop: 8 }} />}
          </Card>
        </Col>
      )}
    </Row>
  );
}
