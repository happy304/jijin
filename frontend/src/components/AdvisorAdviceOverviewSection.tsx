import { Alert, Card, Col, Descriptions, List, Row, Typography } from 'antd';
import type { TradingAdviceItem } from '@/api/advisor';
import {
  adviceStrengthLabel,
  advisorDecisionLabel,
  formatCurrency,
} from '@/utils/advisorDisplay';

const { Text } = Typography;

type AdvisorViewMode = 'novice' | 'expert';

const executionTypeLabel = {
  one_time: '一次性参考',
  batch: '分批参考',
  fixed_investment: '定投参考',
  hold: '暂不操作',
} as const;

function summarizeReasons(reasons: string[] | undefined, maxCount = 3): string[] {
  return (reasons || []).map((item) => String(item || '').trim()).filter(Boolean).slice(0, maxCount);
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

function summarizePrimaryReasons(advice: TradingAdviceItem): string[] {
  const factorReasons = (advice.reasoning?.factors || [])
    .filter((factor) => factor.impact !== 'negative')
    .map((factor) => factor.explanation)
    .filter(Boolean);
  const fallbackReasons = advice.reasoning?.summary ? [advice.reasoning.summary, ...advice.reasons] : advice.reasons;
  return Array.from(new Set([...factorReasons, ...fallbackReasons].map((item) => String(item || '').trim()).filter(Boolean))).slice(0, 3);
}

export function AdvisorAdviceOverviewSection({
  advice,
  viewMode,
}: {
  advice: TradingAdviceItem;
  viewMode: AdvisorViewMode;
}) {
  return (
    <>
      {viewMode === 'novice' && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={`这只基金当前检查结果为“${advisorDecisionLabel(advice)}”，强度 ${adviceStrengthLabel(advice.action, advice.confidence, advice.strength)}。`}
          description={advice.not_investment_advice_disclaimer || advice.reasoning?.summary || summarizeReasons(advice.reasons, 2).join('；') || '仅供个人决策支持，可结合下方参考计划和风险提示独立判断。'}
        />
      )}
      <Row gutter={12} style={{ marginBottom: 12 }}>
        <Col span={8}>
          <Card size="small" type="inner" title="三条主要理由">
            <List
              size="small"
              dataSource={summarizePrimaryReasons(advice)}
              locale={{ emptyText: '暂无主要理由' }}
              renderItem={(item) => <List.Item style={{ padding: '2px 0' }}><Text style={{ fontSize: 12 }}>• {item}</Text></List.Item>}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small" type="inner" title="三条风险提示">
            <List
              size="small"
              dataSource={summarizeReasons(advice.risk_warnings, 3)}
              locale={{ emptyText: '暂无额外风险提示' }}
              renderItem={(item) => <List.Item style={{ padding: '2px 0' }}><Text style={{ fontSize: 12 }}>• {item}</Text></List.Item>}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card size="small" type="inner" title="参考操作">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="决策支持定位">
                {advice.decision_support_only === false ? '参考信息' : '仅供复核，不构成投资建议'}
              </Descriptions.Item>
              <Descriptions.Item label="参考调整金额区间">
                {formatCurrency(advice.trade_amount_min ?? advice.trade_plan?.min_amount ?? advice.suggested_amount)} - {formatCurrency(advice.trade_amount_max ?? advice.trade_plan?.max_amount ?? advice.suggested_amount)}
              </Descriptions.Item>
              <Descriptions.Item label="参考方式">
                {advice.trade_plan ? executionTypeLabel[advice.trade_plan.execution_type] : '按当前检查结果参考'}
              </Descriptions.Item>
            </Descriptions>
            <List
              size="small"
              dataSource={collectExecutionNotes(advice).slice(0, 3)}
              locale={{ emptyText: '暂无补充执行说明' }}
              renderItem={(item) => <List.Item style={{ padding: '2px 0' }}><Text style={{ fontSize: 12 }}>• {item}</Text></List.Item>}
            />
          </Card>
        </Col>
      </Row>
    </>
  );
}
