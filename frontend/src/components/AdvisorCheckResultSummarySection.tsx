import { Card, Col, Row, Statistic } from 'antd';
import { ArrowDownOutlined, ArrowUpOutlined, EyeOutlined } from '@ant-design/icons';
import type { AdvisorAnalyzeResponse } from '@/api/advisor';
import { AdvisorReminderCenter, type AdvisorReminderCategory, type AdvisorReminderCenterItem } from '@/components/AdvisorReminderCenter';
import { AdvisorResultSection } from '@/components/AdvisorResultSection';
import { AdvisorRiskComparisonSection } from '@/components/AdvisorRiskComparisonSection';

export function AdvisorCheckResultSummarySection({
  result,
  reminderItems,
  enabledReminderCategories,
  onChangeReminderCategories,
}: {
  result: AdvisorAnalyzeResponse;
  reminderItems: AdvisorReminderCenterItem[];
  enabledReminderCategories: AdvisorReminderCategory[];
  onChangeReminderCategories: (categories: AdvisorReminderCategory[]) => void;
}) {
  return (
    <AdvisorResultSection title="组合检查结果">
      <AdvisorReminderCenter
        items={reminderItems}
        title="提醒中心"
        enabledCategories={enabledReminderCategories}
        onChangeCategories={onChangeReminderCategories}
      />
      <Row gutter={16} style={{ marginTop: 16, marginBottom: 16 }}>
        <Col span={4}>
          <Card size="small">
            <Statistic title="增配候选" value={result.summary.buy_count} suffix="只" valueStyle={{ color: '#cf1322' }} prefix={<ArrowUpOutlined />} />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic title="减配候选" value={result.summary.sell_count} suffix="只" valueStyle={{ color: '#3f8600' }} prefix={<ArrowDownOutlined />} />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic title="继续观察" value={result.summary.hold_count} suffix="只" />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic title="观察项" value={result.summary.watch_count || 0} suffix="只" valueStyle={{ color: '#1677ff' }} prefix={<EyeOutlined />} />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic title="增配候选额" value={result.summary.total_buy_amount} prefix="¥" precision={0} />
          </Card>
        </Col>
        <Col span={4}>
          <Card size="small">
            <Statistic title="高置信度" value={result.summary.high_confidence_signals} suffix="个" valueStyle={{ color: '#1890ff' }} />
          </Card>
        </Col>
      </Row>
      <AdvisorRiskComparisonSection comparison={result.risk_comparison} />
    </AdvisorResultSection>
  );
}
