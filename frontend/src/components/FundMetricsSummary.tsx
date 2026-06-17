import { Card, Col, Empty, Row, Table } from 'antd';

export interface MetricTableItem {
  key: string;
  label: string;
  value: string;
}

export interface FeeTableItem {
  key: string;
  type: string;
  rate: string;
}

export interface FundMetricsSummaryProps {
  performanceMetrics: MetricTableItem[];
  feeData: FeeTableItem[];
}

export function FundMetricsSummary({ performanceMetrics, feeData }: FundMetricsSummaryProps) {
  return (
    <Row gutter={16}>
      <Col xs={24} md={12}>
        <Card title="业绩指标" style={{ marginBottom: 16 }}>
          {performanceMetrics.length > 0 ? (
            <Table
              dataSource={performanceMetrics}
              columns={[
                { title: '指标', dataIndex: 'label', key: 'label' },
                { title: '数值', dataIndex: 'value', key: 'value' },
              ]}
              pagination={false}
              size="small"
              rowKey="key"
            />
          ) : (
            <Empty description="暂无业绩数据" />
          )}
        </Card>
      </Col>

      <Col xs={24} md={12}>
        <Card title="费率信息" style={{ marginBottom: 16 }}>
          {feeData.length > 0 ? (
            <Table
              dataSource={feeData}
              columns={[
                { title: '费用类型', dataIndex: 'type', key: 'type' },
                { title: '费率', dataIndex: 'rate', key: 'rate' },
              ]}
              pagination={false}
              size="small"
              rowKey="key"
            />
          ) : (
            <Empty description="暂无费率信息" />
          )}
        </Card>
      </Col>
    </Row>
  );
}
