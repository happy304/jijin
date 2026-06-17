import { Table, Typography } from 'antd';
import type {
  AdvisorHoldingImportRowResult,
  AdvisorPositionImportGovernanceSummary,
} from '@/api/advisor';
import { AdvisorPositionImportGovernanceSummaryCard } from '@/components/AdvisorPositionImportGovernanceSummaryCard';

const { Text } = Typography;

export function AdvisorPositionImportFailureContent({
  failedRows,
  governanceSummary,
}: {
  failedRows: AdvisorHoldingImportRowResult[];
  governanceSummary: AdvisorPositionImportGovernanceSummary;
}) {
  return (
    <div>
      <Text type="secondary">已导入的持仓会直接替换当前本地与服务端持仓。请按行号修正失败记录，并按治理提示复核异常持仓。</Text>
      <AdvisorPositionImportGovernanceSummaryCard summary={governanceSummary} />
      {failedRows.length > 0 && (
        <Table<AdvisorHoldingImportRowResult>
          size="small"
          style={{ marginTop: 12 }}
          rowKey="row_number"
          dataSource={failedRows}
          pagination={false}
          columns={[
            { title: '行号', dataIndex: 'row_number', width: 80 },
            { title: '基金', dataIndex: 'fund_code', width: 120, render: (value: string | null) => value || '-' },
            { title: '失败原因', dataIndex: 'error' },
          ]}
        />
      )}
    </div>
  );
}
