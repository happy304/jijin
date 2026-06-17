import { Alert, Card, Empty, Spin } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';

export interface FundNavChartCardProps {
  option: EChartsOption;
  loading?: boolean;
  error?: boolean;
  hasRecords?: boolean;
  needsIngest?: boolean;
}

export function FundNavChartCard({
  option,
  loading,
  error,
  hasRecords,
  needsIngest,
}: FundNavChartCardProps) {
  return (
    <Card title="净值走势（近一年）" style={{ marginBottom: 16 }}>
      {loading ? (
          <Spin>
          <div style={{ minHeight: 120 }} />
        </Spin>
      ) : error ? (
        <Alert type="error" message="净值数据加载失败" showIcon />
      ) : !hasRecords ? (
        <Empty description={needsIngest ? '暂无净值数据，请先在「基金搜索」页面采集该基金' : '暂无净值数据'} />
      ) : (
        <ReactECharts
          option={option}
          style={{ height: 400 }}
          notMerge
        />
      )}
    </Card>
  );
}
