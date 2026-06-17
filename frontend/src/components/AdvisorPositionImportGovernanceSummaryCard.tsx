import { Alert, Card, Space, Tag, Typography } from 'antd';
import type { AdvisorPositionImportGovernanceSummary } from '@/api/advisor';
import { formatCurrency, hasImportGovernanceWarnings } from '@/utils/advisorDisplay';

const { Text } = Typography;

export function AdvisorPositionImportGovernanceSummaryCard({
  summary,
  compact = false,
}: {
  summary?: AdvisorPositionImportGovernanceSummary | null;
  compact?: boolean;
}) {
  if (!summary) return null;

  const hasWarnings = hasImportGovernanceWarnings(summary);

  return (
    <Card size="small" type="inner" title="导入治理诊断" style={{ marginTop: compact ? 8 : 12 }}>
      <Space direction="vertical" size={8} style={{ width: '100%' }}>
        <Space wrap>
          <Tag color="blue">持仓 {summary.position_count} 只</Tag>
          <Tag>成功行 {summary.imported_row_count}</Tag>
          <Tag color="purple">总市值 {formatCurrency(summary.total_market_value)}</Tag>
          <Tag color="cyan">总成本 {formatCurrency(summary.total_cost_basis)}</Tag>
          {summary.duplicate_fund_codes.length > 0 && <Tag color="orange">重复 {summary.duplicate_fund_codes.length} 只</Tag>}
          {summary.zero_value_fund_codes.length > 0 && <Tag color="red">零值 {summary.zero_value_fund_codes.length} 只</Tag>}
          {summary.suspicious_cost_fund_codes.length > 0 && <Tag color="red">成本异常 {summary.suspicious_cost_fund_codes.length} 只</Tag>}
        </Space>
        {hasWarnings ? (
          <Alert
            type="warning"
            showIcon
            message="本次导入存在需要复核的持仓治理提示"
            description={(
              <Space direction="vertical" size={4}>
                {summary.warnings.map((warning) => <Text key={warning} type="secondary">• {warning}</Text>)}
                {summary.duplicate_fund_codes.length > 0 && <Text type="secondary">重复基金：{summary.duplicate_fund_codes.join('、')}</Text>}
                {summary.zero_value_fund_codes.length > 0 && <Text type="secondary">零值持仓：{summary.zero_value_fund_codes.join('、')}</Text>}
                {summary.suspicious_cost_fund_codes.length > 0 && <Text type="secondary">成本异常：{summary.suspicious_cost_fund_codes.join('、')}</Text>}
              </Space>
            )}
          />
        ) : (
          <Alert type="success" showIcon message="未发现重复、零值或明显成本异常持仓" />
        )}
      </Space>
    </Card>
  );
}
