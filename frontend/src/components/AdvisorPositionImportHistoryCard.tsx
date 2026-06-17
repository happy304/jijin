import { Button, Card, Empty, Popconfirm, Table, Tag, Typography } from 'antd';
import type { AdvisorPositionImportHistoryResponse } from '@/api/advisor';
import { AdvisorPositionImportGovernanceSummaryCard as PositionImportGovernanceSummaryCard } from '@/components/AdvisorPositionImportGovernanceSummaryCard';
import {
  getImportGovernanceSummary,
  importHistoryStatusColor,
  importHistoryStatusLabel,
} from '@/utils/advisorDisplay';

const { Text } = Typography;

interface AdvisorPositionImportHistoryCardProps {
  data?: AdvisorPositionImportHistoryResponse;
  loading: boolean;
  restoring: boolean;
  restoringImportId?: number | null;
  onPageChange: (page: number) => void;
  onRestore: (importId: number) => void;
}

export function AdvisorPositionImportHistoryCard({
  data,
  loading,
  restoring,
  restoringImportId,
  onPageChange,
  onRestore,
}: AdvisorPositionImportHistoryCardProps) {
  return (
    <Card size="small" title="最近导入历史" style={{ marginBottom: 12 }}>
      {loading ? (
        <Card loading size="small" variant="borderless" />
      ) : data && data.items.length > 0 ? (
        <Table
          size="small"
          rowKey="id"
          dataSource={data.items}
          pagination={{
            current: data.page,
            pageSize: data.page_size,
            total: data.total,
            onChange: onPageChange,
            showSizeChanger: false,
          }}
          scroll={{ x: 900 }}
          columns={[
            { title: '时间', dataIndex: 'created_at', width: 170, render: (value: string | null) => value ? new Date(value).toLocaleString('zh-CN') : '-' },
            { title: '文件名', dataIndex: 'filename', width: 220, ellipsis: true },
            { title: '格式', dataIndex: 'file_format', width: 80, render: (value: string) => String(value || '-').toUpperCase() },
            { title: '结果', dataIndex: 'status', width: 100, render: (value: string) => <Tag color={importHistoryStatusColor(value)}>{importHistoryStatusLabel(value)}</Tag> },
            { title: '总行数', dataIndex: 'total_rows', width: 80 },
            { title: '成功', dataIndex: 'imported_count', width: 70 },
            { title: '失败', dataIndex: 'failed_count', width: 70 },
            { title: '当前持仓数', dataIndex: 'replaced_position_count', width: 100 },
            {
              title: '操作',
              key: 'actions',
              width: 120,
              render: (_, record) => (
                <Popconfirm
                  title="恢复这次导入的持仓？"
                  description="会用这次导入成功的持仓快照替换当前本地与服务端持仓。"
                  onConfirm={() => onRestore(record.id)}
                >
                  <Button size="small" loading={restoring && restoringImportId === record.id}>
                    恢复这次持仓
                  </Button>
                </Popconfirm>
              ),
            },
          ]}
          expandable={{
            expandedRowRender: (record) => {
              const governanceSummary = getImportGovernanceSummary(record.metadata);
              return (
                <div>
                  <Text type="secondary">本次成功导入 {record.imported_count} 条，失败 {record.failed_count} 条。</Text>
                  <PositionImportGovernanceSummaryCard summary={governanceSummary} compact />
                  {!!record.rows?.length && (
                    <Table
                      size="small"
                      style={{ marginTop: 8 }}
                      rowKey="row_number"
                      dataSource={record.rows}
                      pagination={false}
                      columns={[
                        { title: '行号', dataIndex: 'row_number', width: 80 },
                        { title: '基金', dataIndex: 'fund_code', width: 120, render: (value: string | null) => value || '-' },
                        { title: '状态', dataIndex: 'status', width: 100, render: (value: string) => <Tag color={value === 'created' ? 'green' : 'red'}>{value === 'created' ? '成功' : '失败'}</Tag> },
                        { title: '失败原因', dataIndex: 'error', render: (value: string | null) => value || '-' },
                      ]}
                    />
                  )}
                </div>
              );
            },
          }}
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无导入历史" />
      )}
    </Card>
  );
}
