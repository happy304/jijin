import { Alert, Card, Descriptions, Space, Spin, Tag, Typography } from 'antd';
import type { NavQualityResponse } from '@/api/funds';
import {
  DataFreshnessNotice,
  dataQualityAlertType,
  dataQualityStatusLabel,
  dataQualitySummary,
} from '@/components/DataTrustNotice';

const { Text } = Typography;

export interface FundDataQualityGateProps {
  latestNavDate?: string | null;
  navQuality?: NavQualityResponse | null;
  loading?: boolean;
}

export function FundDataQualityGate({ latestNavDate, navQuality, loading }: FundDataQualityGateProps) {
  return (
    <Card title="分析前数据检查" style={{ marginBottom: 16 }}>
      <DataFreshnessNotice lastDate={latestNavDate} style={{ marginBottom: 12 }} />
      {loading ? (
        <Spin>
          <div style={{ minHeight: 80 }} />
        </Spin>
      ) : !navQuality ? (
        <Alert
          type="info"
          showIcon
          message="暂无数据质量检查结果"
          description="建议先完成净值采集和质量检查，再进行回测、筛选或组合检查。"
        />
      ) : (
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            type={dataQualityAlertType(navQuality.status)}
            showIcon
            message={`NAV 数据质量：${dataQualityStatusLabel(navQuality.status)}`}
            description={dataQualitySummary(navQuality.status)}
          />
          <Descriptions column={{ xs: 1, md: 4 }} bordered size="small">
            <Descriptions.Item label="分析区间">
              {navQuality.start_date || '-'} → {navQuality.end_date || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="净值区间">
              {navQuality.first_nav_date || '-'} → {navQuality.last_nav_date || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="最新净值日期">{navQuality.last_nav_date || '-'}</Descriptions.Item>
            <Descriptions.Item label="NAV 点数">{navQuality.total_nav_points}</Descriptions.Item>
            <Descriptions.Item label="自然日覆盖">
              <Tag color={navQuality.coverage_ratio >= 0.6 ? 'green' : navQuality.coverage_ratio >= 0.4 ? 'orange' : 'red'}>
                {(navQuality.coverage_ratio * 100).toFixed(1)}%
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="复权覆盖">
              <Tag color={navQuality.adj_nav_coverage_ratio >= 0.9 ? 'green' : navQuality.adj_nav_coverage_ratio >= 0.6 ? 'orange' : 'red'}>
                {(navQuality.adj_nav_coverage_ratio * 100).toFixed(1)}%
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="最大缺口">{navQuality.max_gap_days} 天</Descriptions.Item>
            <Descriptions.Item label="异常跳变">{navQuality.spike_count} 次</Descriptions.Item>
            <Descriptions.Item label="单位净值回退">{navQuality.unit_nav_fallback_points} 条</Descriptions.Item>
            <Descriptions.Item label="问题数量">
              <Tag color={navQuality.issues.length > 0 ? 'orange' : 'green'}>{navQuality.issues.length}</Tag>
            </Descriptions.Item>
          </Descriptions>
          <Text type="secondary">
            本提示仅反映当前数据质量和覆盖情况，不代表基金未来表现，也不构成投资建议。
          </Text>
        </Space>
      )}
    </Card>
  );
}
