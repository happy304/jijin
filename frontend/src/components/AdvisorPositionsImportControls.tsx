import { Alert, Button, Divider, Space, Tag, Typography, Upload } from 'antd';
import { UploadOutlined } from '@ant-design/icons';

const { Text } = Typography;

interface AdvisorPositionsImportControlsProps {
  syncing: boolean;
  downloading: boolean;
  importing: boolean;
  onDownloadTemplate: (format: 'csv' | 'xlsx') => void;
  onImportPositions: (file: File) => Promise<boolean>;
}

export function AdvisorPositionsImportControls({
  syncing,
  downloading,
  importing,
  onDownloadTemplate,
  onImportPositions,
}: AdvisorPositionsImportControlsProps) {
  return (
    <>
      <Divider orientation="left" plain>当前持仓（可选 — 填写后建议更精准）</Divider>
      <Space style={{ marginBottom: 12 }} wrap>
        <Text type="secondary">填写持仓市值、买入日期和成本，引擎会考虑浮盈浮亏和赎回费率</Text>
        <Tag color={syncing ? 'processing' : 'blue'}>
          {syncing ? '持仓同步中' : '已启用服务端持仓保存'}
        </Tag>
        <Button
          size="small"
          onClick={() => onDownloadTemplate('csv')}
          loading={downloading}
        >
          下载 CSV 模板
        </Button>
        <Button
          size="small"
          onClick={() => onDownloadTemplate('xlsx')}
          loading={downloading}
        >
          下载 Excel 模板
        </Button>
        <Upload accept=".csv,.xls,.xlsx" showUploadList={false} beforeUpload={onImportPositions}>
          <Button size="small" icon={<UploadOutlined />} loading={importing}>导入持仓 CSV/Excel</Button>
        </Upload>
      </Space>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 12 }}
        message="持仓导入模板字段"
        description="支持 CSV / XLS / XLSX：基金代码、当前市值、持有份额、持仓成本、买入日期。表头可使用中文或英文别名；买入日期格式为 YYYY-MM-DD。导入后会替换当前本地与服务端持仓，若想回滚，可在下方导入历史中一键恢复某次快照。"
      />
    </>
  );
}
