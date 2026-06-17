import { Alert, Space, Tag, Tooltip } from 'antd';
import type {
  AdvisorHistoryItem,
  AdvisorNavDataStaleWarning,
  AdvisorNavQualityWarning,
} from '@/api/advisor';

export interface AdvisorNavDataWarningsProps {
  navDataStale?: AdvisorNavDataStaleWarning | null;
  navQualityWarning?: AdvisorNavQualityWarning | null;
}

export function AdvisorNavDataWarnings({
  navDataStale,
  navQualityWarning,
}: AdvisorNavDataWarningsProps) {
  if (!navDataStale && !navQualityWarning) return null;

  const staleMessage =
    navDataStale?.message || '该建议依赖的历史复权净值已重新计算，建议更新记录以获得最新口径结果。';
  const qualityMessage =
    navQualityWarning?.message || '部分 NAV 数据存在口径混用或质量提示，请谨慎解读建议结果。';
  const affectedFunds = navQualityWarning?.funds
    ? Object.keys(navQualityWarning.funds).join('、')
    : '';

  return (
    <Space direction="vertical" style={{ width: '100%', marginBottom: 16 }}>
      {navDataStale && <Alert type="warning" message="净值数据已更新" description={staleMessage} showIcon />}
      {navQualityWarning && (
        <Alert
          type="warning"
          message="NAV 数据质量提示"
          description={affectedFunds ? `${qualityMessage} 受影响基金：${affectedFunds}` : qualityMessage}
          showIcon
        />
      )}
    </Space>
  );
}

export function advisorNavWarningTooltip(item: AdvisorHistoryItem): string {
  const messages: string[] = [];
  if (item.nav_data_stale) {
    messages.push(item.nav_data_stale.message || '该建议依赖的历史复权净值已重新计算，建议更新记录以获得最新口径结果。');
  }
  if (item.nav_quality_warning) {
    const affectedFunds = item.nav_quality_warning.funds
      ? Object.keys(item.nav_quality_warning.funds).join('、')
      : '';
    const qualityMessage = item.nav_quality_warning.message || '部分 NAV 数据存在口径混用或质量提示，请谨慎解读建议结果。';
    messages.push(affectedFunds ? `${qualityMessage} 受影响基金：${affectedFunds}` : qualityMessage);
  }
  return messages.join('；');
}

export function AdvisorNavWarningTags({ item }: { item: AdvisorHistoryItem }) {
  if (!item.nav_data_stale && !item.nav_quality_warning) return <>-</>;
  return (
    <Tooltip title={advisorNavWarningTooltip(item)}>
      <Space size={4} wrap>
        {item.nav_data_stale && <Tag color="orange">净值已更新</Tag>}
        {item.nav_quality_warning && <Tag color="gold">NAV质量</Tag>}
      </Space>
    </Tooltip>
  );
}
