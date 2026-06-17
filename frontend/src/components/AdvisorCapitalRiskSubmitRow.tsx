import { Button, Col, Form, InputNumber, Row, Select } from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';

const RISK_OPTIONS = [
  { value: 'conservative', label: '保守型 — 高门槛，低仓位' },
  { value: 'moderate', label: '稳健型 — 平衡配置' },
  { value: 'aggressive', label: '进取型 — 低门槛，高仓位' },
];

interface AdvisorCapitalRiskSubmitRowProps {
  loading: boolean;
  submitText: string;
}

export function AdvisorCapitalRiskSubmitRow({ loading, submitText }: AdvisorCapitalRiskSubmitRowProps) {
  return (
    <Row gutter={16}>
      <Col span={8}><Form.Item name="total_capital" label="总可用资金（元）"><InputNumber min={100} step={1000} style={{width:'100%'}}/></Form.Item></Col>
      <Col span={8}><Form.Item name="risk_level" label="风险偏好"><Select options={RISK_OPTIONS}/></Form.Item></Col>
      <Col span={8} style={{display:'flex',alignItems:'flex-end'}}><Form.Item><Button type="primary" htmlType="submit" icon={<ThunderboltOutlined/>} loading={loading}>{submitText}</Button></Form.Item></Col>
    </Row>
  );
}
