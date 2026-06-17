import { Button, Space, Tag, Typography, message } from 'antd';
import { DownloadOutlined } from '@ant-design/icons';
import {
  useDownloadSnapshotVersion,
  useSnapshotVersions,
  type SnapshotVersionResponse,
} from '@/api/advisor';
import { compactHash, formatRequestTime } from '@/utils/advisorDisplay';

const { Text } = Typography;

function triggerBlobDownload(blob: Blob, filename: string): void {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}

function SnapshotDownloadButton({ version, label = '下载原始快照' }: { version: SnapshotVersionResponse; label?: string }) {
  const downloadMutation = useDownloadSnapshotVersion();

  const handleDownload = async () => {
    try {
      const blob = await downloadMutation.mutateAsync(version.version_id);
      triggerBlobDownload(blob, `${version.provider}_${version.fund_code}_${version.endpoint}_${version.snapshot_date}_${version.version_id}.${version.ext}`);
      message.success('已下载原始快照');
    } catch {
      message.error('下载原始快照失败');
    }
  };

  return (
    <Button size="small" icon={<DownloadOutlined />} loading={downloadMutation.isPending} onClick={handleDownload}>
      {label}
    </Button>
  );
}

export function AdvisorSnapshotVersionLookupPanel({
  provider,
  fundCode,
  endpoint,
  asOf,
}: {
  provider?: string | null;
  fundCode: string;
  endpoint: string;
  asOf?: string | null;
}) {
  const { data, isLoading } = useSnapshotVersions({
    provider: provider || undefined,
    fund_code: fundCode,
    endpoint,
    as_of: asOf || undefined,
    limit: 5,
  });

  if (!provider) return <Text type="secondary">-</Text>;
  if (isLoading) return <Text type="secondary">加载中...</Text>;
  if (!data || data.items.length === 0) return <Text type="secondary">暂无索引版本</Text>;

  return (
    <Space direction="vertical" size={6} style={{ width: '100%' }}>
      {data.items.map((item) => (
        <Space key={item.version_id} wrap>
          <Tag color="blue">{item.version_id}</Tag>
          <Tag>{item.snapshot_date}</Tag>
          {item.captured_at ? <Tag>{formatRequestTime(item.captured_at)}</Tag> : null}
          <Tag>{compactHash(item.sha256)}</Tag>
          <SnapshotDownloadButton version={item} label="下载" />
        </Space>
      ))}
    </Space>
  );
}
