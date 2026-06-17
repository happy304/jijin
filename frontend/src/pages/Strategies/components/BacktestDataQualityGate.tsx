import { useMemo } from 'react';
import { Alert, Card, Col, Empty, List, Row, Space, Spin, Statistic, Tag, Typography } from 'antd';
import { useBacktestDataQualityCheck, type BacktestDataQualityFund } from '@/api/backtests';

const { Text } = Typography;

interface BacktestDataQualityGateProps {
  strategyId: number | null | undefined;
  startDate?: string;
  endDate?: string;
  initialCapital?: number;
  fundCount?: number;
  enabled?: boolean;
}

function statusLabel(status: string | undefined): string {
  if (status === 'good') return '良好';
  if (status === 'warning') return '需关注';
  if (status === 'poor') return '较差';
  return status || '未知';
}

function statusColor(status: string | undefined): string {
  if (status === 'good') return 'green';
  if (status === 'warning') return 'gold';
  if (status === 'poor') return 'red';
  return 'default';
}

function alertType(status: string | undefined, canProceed: boolean | undefined): 'success' | 'warning' | 'error' {
  if (status === 'poor' || canProceed === false) return 'error';
  if (status === 'warning') return 'warning';
  return 'success';
}

function formatPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '-';
  return `${(value * 100).toFixed(1)}%`;
}

function summarizeStatusCounts(funds: BacktestDataQualityFund[]): Record<string, number> {
  return funds.reduce<Record<string, number>>((acc, fund) => {
    acc[fund.status] = (acc[fund.status] || 0) + 1;
    return acc;
  }, {});
}

export function BacktestDataQualityGate({
  strategyId,
  startDate,
  endDate,
  initialCapital = 100000,
  fundCount,
  enabled = true,
}: BacktestDataQualityGateProps) {
  const payload = useMemo(() => {
    if (!strategyId || !startDate || !endDate) return null;
    return {
      strategy_id: strategyId,
      start_date: startDate,
      end_date: endDate,
      initial_capital: initialCapital,
    };
  }, [strategyId, startDate, endDate, initialCapital]);

  const { data, isLoading, isFetching, isError, error } = useBacktestDataQualityCheck(payload, enabled);
  const statusCounts = summarizeStatusCounts(data?.funds || []);
  const riskyFunds = (data?.funds || []).filter((fund) => fund.status === 'warning' || fund.status === 'poor');
  const minCoverage = data?.funds?.length ? Math.min(...data.funds.map((fund) => fund.coverage_ratio ?? 0)) : null;
  const maxGapDays = data?.funds?.length ? Math.max(...data.funds.map((fund) => fund.max_gap_days ?? 0)) : null;
  const totalSpikeCount = (data?.funds || []).reduce((sum, fund) => sum + (fund.spike_count || 0), 0);

  if (!strategyId) {
    return (
      <Card size="small" title="回测前数据检查" style={{ marginBottom: 16 }}>
        <Empty description="请先选择策略，系统将基于统一数据质量接口辅助判断回测结果可解释性。" />
      </Card>
    );
  }

  if (!startDate || !endDate) {
    return (
      <Card size="small" title="回测前数据检查" style={{ marginBottom: 16 }}>
        <Empty description="请选择完整回测区间后再进行数据质量检查。" />
      </Card>
    );
  }

  return (
    <Card size="small" title="回测前数据检查" style={{ marginBottom: 16 }}>
      {isLoading && !data ? (
        <Spin tip="通过统一质量接口检查基金池数据..." />
      ) : isError ? (
        <Alert
          type="warning"
          showIcon
          message="数据质量检查失败"
          description={error instanceof Error ? error.message : '暂时无法获取统一质量检查结果，回测仍可提交，但建议稍后复核数据质量。'}
        />
      ) : data ? (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Alert
            type={alertType(data.overall_status, data.can_proceed)}
            showIcon
            message={`统一数据质量状态：${statusLabel(data.overall_status)}`}
            description={
              data.can_proceed
                ? '后端统一质量检查未发现阻断性问题。回测结果仍需结合样本区间、策略假设和数据口径谨慎解读。'
                : '后端统一质量检查认为当前数据不适合直接回测。本轮仍不阻断提交，但宜先补齐或复核数据。'
            }
          />

          <Row gutter={[12, 12]}>
            <Col xs={12} sm={6}>
              <Statistic title="检查基金" value={data.funds.length} suffix={fundCount ? `/ ${fundCount}` : undefined} />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic title="需关注" value={riskyFunds.length} />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic title="最低覆盖率" value={formatPct(minCoverage)} />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic title="最大缺口" value={maxGapDays ?? '-'} suffix={maxGapDays != null ? '天' : undefined} />
            </Col>
          </Row>

          <Space size={6} wrap>
            {Object.entries(statusCounts).map(([status, count]) => (
              <Tag key={status} color={statusColor(status)}>
                {statusLabel(status)} {count}
              </Tag>
            ))}
            <Tag color={data.can_proceed ? 'green' : 'red'}>{data.can_proceed ? '可继续回测' : '建议先复核'}</Tag>
            {totalSpikeCount > 0 && <Tag color="orange">跳变 {totalSpikeCount} 次</Tag>}
            {isFetching && <Tag color="blue">刷新中</Tag>}
          </Space>

          {data.warnings.length > 0 && (
            <List
              size="small"
              dataSource={data.warnings.slice(0, 5)}
              renderItem={(item) => (
                <List.Item style={{ padding: '2px 0' }}>
                  <Text type="secondary" style={{ fontSize: 12 }}>• {item}</Text>
                </List.Item>
              )}
            />
          )}

          {riskyFunds.length > 0 && (
            <Space size={6} wrap>
              {riskyFunds.slice(0, 8).map((fund) => (
                <Tag key={fund.fund_code} color={statusColor(fund.status)}>
                  {fund.fund_code} {statusLabel(fund.status)} / 覆盖 {formatPct(fund.coverage_ratio)}
                </Tag>
              ))}
              {riskyFunds.length > 8 && <Tag>另有 {riskyFunds.length - 8} 只需关注</Tag>}
            </Space>
          )}

          <Text type="secondary" style={{ fontSize: 12 }}>
            本检查来自后端统一回测前质量接口，仅用于数据可靠性提示，不构成投资建议或交易指令；当前不改变回测提交流程和计算口径。
          </Text>
        </Space>
      ) : (
        <Empty description="暂无数据质量检查结果" />
      )}
    </Card>
  );
}
