import { Card, Col, Empty, Row, Spin, Table } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { FundHoldingsResponse, HoldingPositionItem } from '@/api/funds';

export interface FundHoldingsDistributionProps {
  holdingsData?: FundHoldingsResponse | null;
  loading?: boolean;
}

function buildIndustryPieOption(industries: FundHoldingsResponse['industry_distribution']) {
  const threshold = 0.02;
  const major: { name: string; value: number }[] = [];
  let otherWeight = 0;

  for (const item of industries) {
    if (item.weight >= threshold) {
      major.push({
        name: item.industry,
        value: parseFloat((item.weight * 100).toFixed(2)),
      });
    } else {
      otherWeight += item.weight;
    }
  }

  if (otherWeight > 0) {
    major.push({
      name: '其他',
      value: parseFloat((otherWeight * 100).toFixed(2)),
    });
  }

  return {
    tooltip: {
      trigger: 'item',
      formatter: '{b}: {c}%',
    },
    legend: {
      orient: 'vertical',
      right: 10,
      top: 'center',
      textStyle: { fontSize: 12 },
    },
    series: [
      {
        type: 'pie',
        radius: ['45%', '72%'],
        center: ['35%', '50%'],
        avoidLabelOverlap: true,
        itemStyle: {
          borderRadius: 4,
          borderColor: '#fff',
          borderWidth: 2,
        },
        label: {
          show: false,
        },
        emphasis: {
          label: {
            show: true,
            fontSize: 14,
            fontWeight: 'bold',
            formatter: '{b}\n{c}%',
          },
        },
        data: major,
      },
    ],
  };
}

export function FundHoldingsDistribution({ holdingsData, loading }: FundHoldingsDistributionProps) {
  return (
    <Card title={`持仓分布${holdingsData?.report_date ? `（${holdingsData.report_date}）` : ''}`} style={{ marginBottom: 16 }}>
      {loading ? (
        <Spin>
          <div style={{ minHeight: 120 }} />
        </Spin>
      ) : !holdingsData?.positions?.length ? (
        <Empty description="暂无持仓数据（货币基金或数据未披露）" />
      ) : (
        <>
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col xs={8}>
              <Card size="small" variant="borderless" style={{ background: '#f6ffed' }}>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 20, fontWeight: 'bold', color: '#52c41a' }}>
                    {(holdingsData.top5_concentration * 100).toFixed(2)}%
                  </div>
                  <div style={{ color: '#666', fontSize: 12 }}>前5大集中度</div>
                </div>
              </Card>
            </Col>
            <Col xs={8}>
              <Card size="small" variant="borderless" style={{ background: '#e6f7ff' }}>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 20, fontWeight: 'bold', color: '#1890ff' }}>
                    {(holdingsData.top10_concentration * 100).toFixed(2)}%
                  </div>
                  <div style={{ color: '#666', fontSize: 12 }}>前10大集中度</div>
                </div>
              </Card>
            </Col>
            <Col xs={8}>
              <Card size="small" variant="borderless" style={{ background: '#fff7e6' }}>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 20, fontWeight: 'bold', color: '#fa8c16' }}>
                    {holdingsData.total_stocks}
                  </div>
                  <div style={{ color: '#666', fontSize: 12 }}>持股总数</div>
                </div>
              </Card>
            </Col>
          </Row>

          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Card size="small" title="行业分布" variant="borderless">
                {holdingsData.industry_distribution.length > 0 ? (
                  <ReactECharts
                    option={buildIndustryPieOption(holdingsData.industry_distribution)}
                    style={{ height: 300 }}
                    notMerge
                  />
                ) : (
                  <Empty description="无行业分类数据" />
                )}
              </Card>
            </Col>

            <Col xs={24} md={12}>
              <Card size="small" title="重仓股明细" variant="borderless">
                <Table<HoldingPositionItem>
                  dataSource={holdingsData.positions.slice(0, 10)}
                  columns={[
                    {
                      title: '股票名称',
                      dataIndex: 'stock_name',
                      key: 'stock_name',
                      render: (text, record) => text || record.stock_code,
                    },
                    {
                      title: '占比',
                      dataIndex: 'weight',
                      key: 'weight',
                      render: (val) => `${(val * 100).toFixed(2)}%`,
                      sorter: (a, b) => a.weight - b.weight,
                      defaultSortOrder: 'descend' as const,
                    },
                    {
                      title: '行业',
                      dataIndex: 'industry',
                      key: 'industry',
                      render: (text) => text || '-',
                    },
                  ]}
                  pagination={false}
                  size="small"
                  rowKey="stock_code"
                />
              </Card>
            </Col>
          </Row>
        </>
      )}
    </Card>
  );
}
