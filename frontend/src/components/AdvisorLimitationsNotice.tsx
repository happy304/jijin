import { Collapse, List, Typography } from 'antd';

const { Panel } = Collapse;
const { Text } = Typography;

export function AdvisorLimitationsNotice({ limitations }: { limitations?: string[] }) {
  if (!limitations || limitations.length === 0) return null;

  return (
    <Collapse ghost style={{marginTop:8}}>
      <Panel header={<Text type="secondary" style={{fontSize:12}}>模型局限性（{limitations.length}条）</Text>} key="lim">
        <List size="small" dataSource={limitations} renderItem={item=><List.Item style={{padding:'2px 0'}}><Text type="secondary" style={{fontSize:11}}>• {item}</Text></List.Item>}/>
      </Panel>
    </Collapse>
  );
}
