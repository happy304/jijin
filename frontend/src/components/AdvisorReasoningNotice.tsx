import { Alert, Space, Tag, Typography } from 'antd';
import { WarningOutlined } from '@ant-design/icons';
import type { AdvisorAnalyzeResponse } from '@/api/advisor';
import { formatDateWithWeekday, formatRequestTime } from '@/utils/advisorDisplay';

const { Text } = Typography;

type AdvisorTradingTime = NonNullable<AdvisorAnalyzeResponse['trading_time']>;

export function AdvisorReasoningNotice({ tradingTime }: { tradingTime?: AdvisorTradingTime | null }) {
  if (!tradingTime) {
    return (
      <Text type="secondary">
        本页仅提供个人研究与决策辅助，不构成收益承诺或自动下单指令。展开下方单只基金检查结果，可查看主要理由、风险提示、信号贡献与过拟合诊断。
      </Text>
    );
  }

  return (
    <Alert
      type="warning"
      showIcon
      icon={<WarningOutlined />}
      message={`申赎受理 T 日: ${tradingTime.accepted_trade_date ? formatDateWithWeekday(tradingTime.accepted_trade_date) : tradingTime.effective_date}`}
      description={(
        <div>
          <div>{tradingTime.cutoff_info}</div>
          <div style={{ marginTop: 6 }}>本系统仅作为个人研究与决策辅助，不保证收益，也不会替代人工确认下单。</div>
          <Space size={8} wrap style={{ marginTop: 6 }}>
            {tradingTime.request_time && <Tag>当前: {formatRequestTime(tradingTime.request_time)}</Tag>}
            <Tag>截止: {tradingTime.cutoff_time || '15:00:00'}</Tag>
            {typeof tradingTime.is_after_cutoff === 'boolean' && (
              <Tag color={tradingTime.is_after_cutoff ? 'orange' : 'green'}>
                {tradingTime.is_after_cutoff ? '已过截止' : '截止前'}
              </Tag>
            )}
            {tradingTime.nav_date && <Tag color="blue">净值日: {formatDateWithWeekday(tradingTime.nav_date)}</Tag>}
            {tradingTime.expected_confirm_date && <Tag>确认: {formatDateWithWeekday(tradingTime.expected_confirm_date)}</Tag>}
            {tradingTime.expected_settlement_date && <Tag>到账: {formatDateWithWeekday(tradingTime.expected_settlement_date)}</Tag>}
            {!tradingTime.expected_settlement_date && tradingTime.expected_available_date && <Tag>可用: {formatDateWithWeekday(tradingTime.expected_available_date)}</Tag>}
          </Space>
          {tradingTime.rule_basis && <div style={{ marginTop: 6 }}>{tradingTime.rule_basis}</div>}
          {tradingTime.warnings && tradingTime.warnings.length > 0 && (
            <div style={{ marginTop: 6 }}>{tradingTime.warnings.join('；')}</div>
          )}
        </div>
      )}
    />
  );
}
