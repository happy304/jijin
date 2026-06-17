import { Col, Divider, Form, InputNumber, Row, Select, Switch } from 'antd';

const INVESTMENT_GOAL_OPTIONS = [
  { value: 'cash_management', label: '现金管理' },
  { value: 'stable_growth', label: '稳健增值' },
  { value: 'balanced', label: '均衡配置' },
  { value: 'long_term_growth', label: '长期成长' },
];

const INVESTMENT_HORIZON_OPTIONS = [
  { value: 'within_3_months', label: '3个月以内' },
  { value: '3_to_12_months', label: '3-12个月' },
  { value: '1_to_3_years', label: '1-3年' },
  { value: 'over_3_years', label: '3年以上' },
];

const LIQUIDITY_NEED_OPTIONS = [
  { value: 'high', label: '高' },
  { value: 'medium', label: '中' },
  { value: 'low', label: '低' },
];

const TOLERANCE_OPTIONS = [
  { value: 'low', label: '低' },
  { value: 'medium', label: '中' },
  { value: 'high', label: '高' },
];

export function AdvisorInvestmentProfileFields() {
  return (
    <>
      <Divider orientation="left" plain>投资画像（可选）</Divider>
      <Row gutter={16}>
        <Col span={6}><Form.Item name="investment_goal" label="投资目标"><Select allowClear options={INVESTMENT_GOAL_OPTIONS}/></Form.Item></Col>
        <Col span={6}><Form.Item name="investment_horizon" label="投资期限"><Select allowClear options={INVESTMENT_HORIZON_OPTIONS}/></Form.Item></Col>
        <Col span={6}><Form.Item name="liquidity_need" label="流动性需求"><Select allowClear options={LIQUIDITY_NEED_OPTIONS}/></Form.Item></Col>
        <Col span={6}><Form.Item name="max_drawdown_tolerance" label="可承受最大回撤"><InputNumber min={0} max={0.8} step={0.01} style={{width:'100%'}} placeholder="0.08=8%"/></Form.Item></Col>
      </Row>
      <Row gutter={16}>
        <Col span={6}><Form.Item name="monthly_invest_amount" label="每月可投资金额"><InputNumber min={0} step={500} style={{width:'100%'}} placeholder="可选"/></Form.Item></Col>
        <Col span={6}><Form.Item name="industry_concentration_tolerance" label="集中度容忍度"><Select allowClear options={TOLERANCE_OPTIONS}/></Form.Item></Col>
        <Col span={6}><Form.Item name="qdii_fx_risk_tolerance" label="QDII 汇率风险"><Select allowClear options={TOLERANCE_OPTIONS}/></Form.Item></Col>
        <Col span={6}><Form.Item name="fee_sensitivity" label="费率敏感度"><Select allowClear options={TOLERANCE_OPTIONS}/></Form.Item></Col>
      </Row>
      <Form.Item name="compare_risk_levels" label="同时生成三档风险对比" valuePropName="checked">
        <Switch />
      </Form.Item>
    </>
  );
}
