import { Alert, Collapse, Descriptions, List, Typography } from 'antd';
import { WarningOutlined } from '@ant-design/icons';
import type { TradingAdviceItem } from '@/api/advisor';
import { formatRequestTime } from '@/utils/advisorDisplay';

const { Panel } = Collapse;
const { Text } = Typography;

export function AdvisorValidityRiskNotice({ advice }: { advice: TradingAdviceItem }) {
  return (
    <>
      {advice.validity && (
        <Collapse ghost style={{ marginBottom: 8 }}>
          <Panel header={`检查结果有效期至 ${advice.validity.valid_until}`} key="validity">
            <Descriptions column={2} size="small">
              <Descriptions.Item label="生成时间">{formatRequestTime(advice.validity.generated_at)}</Descriptions.Item>
              <Descriptions.Item label="数据截至">{advice.validity.data_as_of}</Descriptions.Item>
            </Descriptions>
            <List size="small" dataSource={advice.validity.invalidation_rules} renderItem={(item)=><List.Item style={{ padding: '2px 0' }}><Text type="secondary" style={{ fontSize: 12 }}>• {item}</Text></List.Item>} />
          </Panel>
        </Collapse>
      )}
      {advice.risk_warnings.length > 0 && (
        <Alert
          type="warning"
          showIcon
          icon={<WarningOutlined/>}
          message="风险提示"
          description={
            <ul style={{margin:0,paddingLeft:16}}>
              {advice.risk_warnings.map((w, i) => <li key={i} style={{fontSize:12}}>{w}</li>)}
            </ul>
          }
          style={{marginBottom:12}}
        />
      )}
    </>
  );
}
