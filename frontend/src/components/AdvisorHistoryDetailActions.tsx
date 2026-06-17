import { Button, Popconfirm, Space } from 'antd';
import { DownloadOutlined, ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons';

export function AdvisorHistoryDetailActions({
  refreshing,
  onBack,
  onRefresh,
  onExportAudit,
  onLoadToForm,
}: {
  refreshing: boolean;
  onBack: () => void;
  onRefresh: () => void;
  onExportAudit: () => void;
  onLoadToForm: () => void;
}) {
  return (
    <Space style={{ marginBottom: 16 }} wrap>
      <Button onClick={onBack}>← 返回列表</Button>
      <Popconfirm
        title="确认更新这条检查记录？"
        description="将按最新数据重算并覆盖当前记录，同时清空旧跟踪结果。"
        onConfirm={onRefresh}
      >
        <Button icon={<ReloadOutlined />} loading={refreshing}>更新记录</Button>
      </Popconfirm>
      <Button icon={<DownloadOutlined />} onClick={onExportAudit}>导出审计 JSON</Button>
      <Button type="primary" icon={<ThunderboltOutlined />} onClick={onLoadToForm}>
        加载到表单并修改
      </Button>
    </Space>
  );
}
