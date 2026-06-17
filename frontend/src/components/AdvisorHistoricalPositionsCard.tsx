import { Card, Table } from 'antd';

type HistoricalPositionInfo = {
  market_value?: number;
  shares?: number;
  cost_basis?: number;
  amount?: number;
  cost?: number;
  buy_date?: string;
};

type HistoricalPositionRow = HistoricalPositionInfo & { code: string };

export function AdvisorHistoricalPositionsCard({
  positionsDetail,
}: {
  positionsDetail?: Record<string, HistoricalPositionInfo> | null;
}) {
  if (!positionsDetail || Object.keys(positionsDetail).length === 0) return null;

  return (
    <Card size="small" title="当时持仓" style={{ marginBottom: 16 }}>
      <Table<HistoricalPositionRow>
        size="small"
        dataSource={Object.entries(positionsDetail).map(([code, info]) => ({ code, ...info }))}
        rowKey="code"
        pagination={false}
        columns={[
          { title: '基金代码', dataIndex: 'code', width: 100 },
          { title: '市值', dataIndex: 'market_value', width: 100, render: (value, row) => `¥${(value ?? row.amount ?? 0).toLocaleString()}` },
          { title: '份额', dataIndex: 'shares', width: 100, render: (value, row) => (value ?? row.amount) ? `${(value ?? row.amount ?? 0).toLocaleString()}` : '-' },
          { title: '成本', dataIndex: 'cost_basis', width: 100, render: (value, row) => (value ?? row.cost) ? `¥${(value ?? row.cost ?? 0).toLocaleString()}` : '-' },
          { title: '买入日期', dataIndex: 'buy_date', width: 120, render: (value) => value || '-' },
        ]}
      />
    </Card>
  );
}
