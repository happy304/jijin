import { Alert, Card, Space, Table, Tag, Typography } from 'antd';
import type { AdvisorAnalyzeResponse } from '@/api/advisor';
import {
  DataFreshnessNotice,
  ResultInterpretationNotice,
  dataQualityStatusLabel,
  dataQualityTagColor,
} from '@/components/DataTrustNotice';

const { Text } = Typography;

export function AdvisorDataTrustPanel({ result }: { result: Pick<AdvisorAnalyzeResponse, 'advices' | 'execution_context'> }) {
  const trust = result.execution_context?.data_trust;
  const runtimeHealth = result.execution_context?.runtime_health as { queue?: { status?: string; warnings?: string[] } } | undefined;
  const qualityItems = result.advices
    .map((advice) => ({
      fund_code: advice.fund_code,
      fund_name: advice.fund_name,
      data_quality: advice.data_quality,
    }))
    .filter((item) => item.data_quality);

  if (qualityItems.length === 0) {
    return (
      <Card title="组合检查数据可信度" size="small" style={{ marginBottom: 16 }}>
        <Alert
          type="info"
          showIcon
          message="暂无统一数据质量摘要"
          description="当前组合检查结果未返回逐基金数据质量摘要，请优先查看单只基金详情、风险提示和模型局限说明。"
        />
      </Card>
    );
  }

  const latestDataEnd = qualityItems
    .map((item) => item.data_quality?.data_end)
    .filter((value): value is string => Boolean(value))
    .sort((a, b) => b.localeCompare(a))[0] || null;
  const navCounts = qualityItems
    .map((item) => item.data_quality?.nav_count)
    .filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  const minNavCount = navCounts.length > 0 ? Math.min(...navCounts) : undefined;
  const warningItems = qualityItems.filter((item) => {
    const quality = item.data_quality;
    return quality?.status !== 'good' || (quality.warnings || []).length > 0 || quality.sample_sufficient === false;
  });
  const poorCount = qualityItems.filter((item) => item.data_quality?.status === 'poor').length;
  const warningCount = qualityItems.filter((item) => item.data_quality?.status === 'warning').length;
  const insufficientCount = qualityItems.filter((item) => item.data_quality?.sample_sufficient === false).length;

  return (
    <Card title="组合检查数据可信度" size="small" style={{ marginBottom: 16 }}>
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {trust && (
          <Alert
            type={trust.level === 'low' ? 'error' : trust.level === 'medium' ? 'warning' : 'success'}
            showIcon
            message={`本次输入数据可信度：${trust.level === 'high' ? '高' : trust.level === 'medium' ? '中' : '低'}（${Math.round((trust.score || 0) * 100)}分）`}
            description={[
              trust.stale_funds?.length ? `数据滞后：${trust.stale_funds.join('、')}` : '',
              trust.missing_oos_snapshot_funds?.length ? `缺少样本外快照：${trust.missing_oos_snapshot_funds.join('、')}` : '',
              ...(trust.warnings || []).slice(0, 2),
            ].filter(Boolean).join('；') || '当前输入数据质量未触发显著降级。'}
          />
        )}
        {runtimeHealth?.queue && runtimeHealth.queue.status !== 'healthy' && (
          <Alert
            type="warning"
            showIcon
            message="运行时队列健康度降级"
            description={(runtimeHealth.queue.warnings || []).join('；') || 'Redis/Celery 队列不可用或状态未知，后台刷新和进度推送可能受影响。'}
          />
        )}
        <DataFreshnessNotice lastDate={latestDataEnd} />
        <ResultInterpretationNotice
          navQualityWarning={warningItems.length > 0 ? { funds: Object.fromEntries(warningItems.map((item) => [item.fund_code, item.data_quality])) } : null}
          tradingDays={minNavCount}
        />
        <Space wrap>
          <Tag color="blue">检查基金 {qualityItems.length} 只</Tag>
          <Tag color={poorCount > 0 ? 'red' : 'default'}>较差 {poorCount}</Tag>
          <Tag color={warningCount > 0 ? 'orange' : 'default'}>需关注 {warningCount}</Tag>
          <Tag color={insufficientCount > 0 ? 'gold' : 'default'}>样本不足 {insufficientCount}</Tag>
          {latestDataEnd && <Tag color="green">最新数据 {latestDataEnd}</Tag>}
        </Space>
        <Table
          size="small"
          rowKey="fund_code"
          pagination={qualityItems.length > 6 ? { pageSize: 6, showSizeChanger: false } : false}
          dataSource={qualityItems}
          columns={[
            {
              title: '基金',
              key: 'fund',
              width: 180,
              render: (_, row: typeof qualityItems[number]) => (
                <div>
                  <Text strong>{row.fund_code}</Text><br />
                  <Text type="secondary" style={{ fontSize: 12 }}>{row.fund_name || '-'}</Text>
                </div>
              ),
            },
            {
              title: '质量状态',
              key: 'status',
              width: 100,
              render: (_, row: typeof qualityItems[number]) => (
                <Tag color={dataQualityTagColor(row.data_quality?.status)}>{dataQualityStatusLabel(row.data_quality?.status)}</Tag>
              ),
            },
            { title: '数据区间', key: 'range', width: 190, render: (_, row: typeof qualityItems[number]) => `${row.data_quality?.data_start || '-'} → ${row.data_quality?.data_end || '-'}` },
            { title: 'NAV点数', key: 'nav_count', width: 90, align: 'right' as const, render: (_, row: typeof qualityItems[number]) => row.data_quality?.nav_count ?? '-' },
            { title: '最大缺口', key: 'max_gap_days', width: 90, align: 'right' as const, render: (_, row: typeof qualityItems[number]) => row.data_quality?.max_gap_days != null ? `${row.data_quality.max_gap_days} 天` : '-' },
            { title: '跳变', key: 'spike_count', width: 80, align: 'right' as const, render: (_, row: typeof qualityItems[number]) => row.data_quality?.spike_count ?? '-' },
            { title: '提示', key: 'warnings', render: (_, row: typeof qualityItems[number]) => (row.data_quality?.warnings || []).slice(0, 2).join('；') || '-' },
          ]}
        />
        <Text type="secondary" style={{ fontSize: 12 }}>
          数据可信度仅用于判断本次组合检查结果的输入质量，不代表基金未来表现，也不构成投资建议或交易指令。
        </Text>
      </Space>
    </Card>
  );
}
