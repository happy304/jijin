import { Button, Card, Empty, Popconfirm, Space, Table, Tag, Typography } from 'antd';
import { DeleteOutlined, EyeOutlined, ReloadOutlined } from '@ant-design/icons';
import type { AdvisorHistoryItem } from '@/api/advisor';
import { AdvisorNavWarningTags } from '@/components/AdvisorNavDataWarnings';

const { Text } = Typography;

export function AdvisorHistoryListCard({
  items,
  total,
  loading,
  currentPage,
  refreshing,
  refreshingId,
  onPageChange,
  onView,
  onRefresh,
  onDelete,
}: {
  items: AdvisorHistoryItem[];
  total: number;
  loading: boolean;
  currentPage: number;
  refreshing: boolean;
  refreshingId?: number | null;
  onPageChange: (page: number) => void;
  onView: (item: AdvisorHistoryItem) => void;
  onRefresh: (id: number) => void;
  onDelete: (id: number) => void;
}) {
  return (
    <Card title="已保存的检查记录">
      {items.length > 0 ? (
        <Table<AdvisorHistoryItem>
          dataSource={items}
          rowKey="id"
          loading={loading}
          scroll={{ x: 1170 }}
          pagination={{
            current: currentPage,
            pageSize: 10,
            total,
            onChange: onPageChange,
          }}
          columns={[
            { title: '日期', dataIndex: 'advice_date', key: 'date', width: 110 },
            { title: '基金', key: 'funds', width: 200, render: (_, row) => <Text style={{ fontSize: 12 }}>{row.fund_codes.slice(0, 5).join(', ')}{row.fund_codes.length > 5 ? ` +${row.fund_codes.length - 5}` : ''}</Text> },
            { title: '风险', dataIndex: 'risk_level', key: 'risk', width: 80, render: (value: string) => <Tag>{value === 'conservative' ? '保守' : value === 'aggressive' ? '进取' : '稳健'}</Tag> },
            { title: '资金', dataIndex: 'total_capital', key: 'capital', width: 100, render: (value: number) => `¥${value.toLocaleString()}` },
            { title: '策略', dataIndex: 'strategy_name', key: 'strategy', width: 120, render: (value: string | null) => value || '-' },
            { title: '增/减/观', key: 'summary', width: 100, render: (_, row) => <Space size={4}><Tag color="red">{row.summary.buy_count}</Tag><Tag color="green">{row.summary.sell_count}</Tag><Tag>{row.summary.hold_count}</Tag></Space> },
            { title: '数据提示', key: 'nav_warning', width: 150, render: (_, row) => <AdvisorNavWarningTags item={row} /> },
            { title: '更新时间', key: 'updated', width: 170, render: (_, row) => {
              const value = row.updated_at || row.created_at;
              return value ? new Date(value).toLocaleString('zh-CN') : '-';
            } },
            { title: '操作', key: 'actions', width: 240, render: (_, row) => (
              <Space>
                <Button size="small" icon={<EyeOutlined />} onClick={() => onView(row)}>查看</Button>
                <Popconfirm
                  title="确认更新这条检查记录？"
                  description="将按最新数据重算并覆盖当前记录，同时清空旧跟踪结果。"
                  onConfirm={() => onRefresh(row.id)}
                >
                  <Button size="small" icon={<ReloadOutlined />} loading={refreshing && refreshingId === row.id}>更新</Button>
                </Popconfirm>
                <Popconfirm title="确定删除？" onConfirm={() => onDelete(row.id)}>
                  <Button size="small" danger icon={<DeleteOutlined />} />
                </Popconfirm>
              </Space>
            ) },
          ]}
        />
      ) : (
        <Empty description="暂无保存的检查记录，生成组合检查后点击「保存检查结果」按钮即可保存" />
      )}
    </Card>
  );
}
