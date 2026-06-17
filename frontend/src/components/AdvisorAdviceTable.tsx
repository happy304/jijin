import type { ReactNode } from 'react';
import { Table } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { TradingAdviceItem } from '@/api/advisor';

export function AdvisorAdviceTable({
  columns,
  advices,
  scrollX,
  expandedRowRender,
}: {
  columns: ColumnsType<TradingAdviceItem>;
  advices: TradingAdviceItem[];
  scrollX: number;
  expandedRowRender: (record: TradingAdviceItem) => ReactNode;
}) {
  return (
    <Table<TradingAdviceItem>
      columns={columns}
      dataSource={advices}
      rowKey="fund_code"
      size="middle"
      scroll={{ x: scrollX }}
      pagination={false}
      expandable={{ expandedRowRender }}
    />
  );
}
