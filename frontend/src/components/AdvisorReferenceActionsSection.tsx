import type { ReactNode } from 'react';
import { Button, Space } from 'antd';
import { DownloadOutlined, SaveOutlined } from '@ant-design/icons';
import { AdvisorResultSection } from '@/components/AdvisorResultSection';

export function AdvisorReferenceActionsSection({
  saving,
  hasHighRiskAdvice,
  onExportAudit,
  onSave,
  children,
}: {
  saving: boolean;
  hasHighRiskAdvice: boolean;
  onExportAudit: () => void;
  onSave: () => void;
  children: ReactNode;
}) {
  return (
    <AdvisorResultSection
      title="复核参考"
      extra={(
        <Space wrap>
          <Button icon={<DownloadOutlined />} onClick={onExportAudit}>导出审计 JSON</Button>
          <Button type="primary" icon={<SaveOutlined />} onClick={onSave} loading={saving} danger={hasHighRiskAdvice}>
            保存检查结果
          </Button>
        </Space>
      )}
    >
      {children}
    </AdvisorResultSection>
  );
}
