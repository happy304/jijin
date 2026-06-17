import { Alert, Card, Col, Empty, Row, Spin, Table, Tag } from 'antd';
import type { NavQualityResponse } from '@/api/funds';
import { dataQualityStatusLabel, dataQualityTagColor } from '@/components/DataTrustNotice';

export interface FundDataQualitySnapshotProps {
  navQuality?: NavQualityResponse | null;
  loading?: boolean;
}

export function FundDataQualitySnapshot({ navQuality, loading }: FundDataQualitySnapshotProps) {
  return (
    <Card title="数据质量快照（近一年）" style={{ marginBottom: 16 }}>
      {loading ? (
        <Spin>
          <div style={{ minHeight: 80 }} />
        </Spin>
      ) : !navQuality ? (
        <Empty description="暂无数据质量信息" />
      ) : (
        <>
          <Row gutter={[16, 16]} style={{ marginBottom: 12 }}>
            <Col xs={12} md={4}>
              <Card size="small" variant="borderless">
                <div style={{ fontSize: 12, color: '#666' }}>质量状态</div>
                <Tag color={dataQualityTagColor(navQuality.status)} style={{ marginTop: 6 }}>
                  {dataQualityStatusLabel(navQuality.status)}
                </Tag>
              </Card>
            </Col>
            <Col xs={12} md={4}>
              <Card size="small" variant="borderless">
                <div style={{ fontSize: 12, color: '#666' }}>NAV 点数</div>
                <div style={{ fontSize: 20, fontWeight: 600 }}>{navQuality.total_nav_points}</div>
              </Card>
            </Col>
            <Col xs={12} md={4}>
              <Card size="small" variant="borderless">
                <div style={{ fontSize: 12, color: '#666' }}>自然日覆盖</div>
                <div style={{ fontSize: 20, fontWeight: 600 }}>{(navQuality.coverage_ratio * 100).toFixed(1)}%</div>
              </Card>
            </Col>
            <Col xs={12} md={4}>
              <Card size="small" variant="borderless">
                <div style={{ fontSize: 12, color: '#666' }}>adj_nav 覆盖</div>
                <div style={{ fontSize: 20, fontWeight: 600 }}>{(navQuality.adj_nav_coverage_ratio * 100).toFixed(1)}%</div>
              </Card>
            </Col>
            <Col xs={12} md={4}>
              <Card size="small" variant="borderless">
                <div style={{ fontSize: 12, color: '#666' }}>最大缺口</div>
                <div style={{ fontSize: 20, fontWeight: 600 }}>{navQuality.max_gap_days} 天</div>
              </Card>
            </Col>
            <Col xs={12} md={4}>
              <Card size="small" variant="borderless">
                <div style={{ fontSize: 12, color: '#666' }}>跳变次数</div>
                <div style={{ fontSize: 20, fontWeight: 600 }}>{navQuality.spike_count}</div>
              </Card>
            </Col>
          </Row>
          {navQuality.issues.length > 0 ? (
            <Table
              size="small"
              pagination={false}
              dataSource={navQuality.issues.slice(0, 8)}
              rowKey={(row) => [row.issue_type, row.trade_date, row.start_date, row.end_date, row.message].filter(Boolean).join('-')}
              columns={[
                { title: '类型', dataIndex: 'issue_type', width: 130 },
                {
                  title: '严重度',
                  dataIndex: 'severity',
                  width: 90,
                  render: (value: string) => <Tag color={dataQualityTagColor(value)}>{dataQualityStatusLabel(value)}</Tag>,
                },
                {
                  title: '日期',
                  key: 'date',
                  width: 180,
                  render: (_, row) => row.trade_date || (row.start_date && row.end_date ? `${row.start_date} ~ ${row.end_date}` : '-'),
                },
                { title: '说明', dataIndex: 'message' },
              ]}
            />
          ) : (
            <Alert type="success" showIcon message="未发现明显 NAV 质量问题" />
          )}
        </>
      )}
    </Card>
  );
}
