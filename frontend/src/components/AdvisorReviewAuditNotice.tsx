import { Alert, Collapse, List, Typography } from 'antd';
import { InfoCircleOutlined } from '@ant-design/icons';

const { Text } = Typography;
const { Panel } = Collapse;

export function AdvisorReviewAuditNotice({
  disclaimer,
  limitations,
}: {
  disclaimer?: string | null;
  limitations?: string[] | null;
}) {
  return (
    <>
      <Alert
        type="warning"
        icon={<InfoCircleOutlined />}
        message="免责声明"
        description={disclaimer}
        showIcon
        style={{ marginBottom: 16 }}
      />
      <Alert type="info" showIcon message="可先保存本次检查结果，再到「历史记录」中查看复盘、收益跟踪和完整审计。" />
      {limitations && limitations.length > 0 && (
        <Collapse ghost style={{ marginTop: 8 }}>
          <Panel header="模型局限性说明" key="limitations">
            <List
              size="small"
              dataSource={limitations}
              renderItem={(item) => (
                <List.Item>
                  <Text type="secondary">• {item}</Text>
                </List.Item>
              )}
            />
          </Panel>
        </Collapse>
      )}
    </>
  );
}
