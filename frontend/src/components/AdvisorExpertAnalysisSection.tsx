import { Card, Col, Collapse, Descriptions, List, Row, Space, Tag, Typography } from 'antd';
import type { TradingAdviceItem } from '@/api/advisor';
import { formatPct } from '@/utils/advisorDisplay';

const { Panel } = Collapse;
const { Text } = Typography;

export function AdvisorExpertAnalysisSection({ advice }: { advice: TradingAdviceItem }) {
  return (
    <>
      <Row gutter={24}>
        {advice.momentum_analysis && (
          <Col span={8}>
            <Card size="small" title="动量分析" type="inner">
              <Descriptions column={1} size="small">
                <Descriptions.Item label="5日收益">{formatPct(advice.momentum_analysis.return_5d)}</Descriptions.Item>
                <Descriptions.Item label="20日收益">{formatPct(advice.momentum_analysis.return_20d)}</Descriptions.Item>
                <Descriptions.Item label="60日收益">{formatPct(advice.momentum_analysis.return_60d)}</Descriptions.Item>
                <Descriptions.Item label="波动率">{formatPct(advice.momentum_analysis.current_vol)}</Descriptions.Item>
                <Descriptions.Item label="波动率分位">{formatPct(advice.momentum_analysis.vol_percentile)}</Descriptions.Item>
                <Descriptions.Item label="市场状态">
                  <Tag color={
                    advice.momentum_analysis.regime === 'trending_up' ? 'red' :
                    advice.momentum_analysis.regime === 'trending_down' ? 'green' :
                    advice.momentum_analysis.regime === 'mean_reverting' ? 'orange' : 'default'
                  }>{advice.momentum_analysis.regime}</Tag>
                </Descriptions.Item>
              </Descriptions>
            </Card>
          </Col>
        )}
        {advice.prediction && (
          <Col span={8}>
            <Card size="small" title="Bootstrap 预测" type="inner" extra={<Tag color="blue">{advice.prediction.sample_size}样本</Tag>}>
              <Descriptions column={1} size="small">
                <Descriptions.Item label="30日预期收益">{formatPct(advice.prediction.expected_return_30d)}</Descriptions.Item>
                <Descriptions.Item label="30日正收益概率">
                  <Text style={{color: (advice.prediction.prob_positive_30d||0) > 0.6 ? '#cf1322' : (advice.prediction.prob_positive_30d||0) < 0.4 ? '#3f8600' : '#666'}}>
                    {formatPct(advice.prediction.prob_positive_30d)}
                  </Text>
                </Descriptions.Item>
                <Descriptions.Item label="30日VaR(95%)">{formatPct(advice.prediction.var_95_30d)}</Descriptions.Item>
                <Descriptions.Item label="30日CVaR(95%)">{formatPct(advice.prediction.cvar_95_30d)}</Descriptions.Item>
                <Descriptions.Item label="置信区间宽度">{formatPct(advice.prediction.confidence_band_width)}</Descriptions.Item>
              </Descriptions>
              <Text type="secondary" style={{fontSize:11}}>{advice.prediction.note}</Text>
            </Card>
          </Col>
        )}
        {advice.risk_position && (
          <Col span={8}>
            <Card size="small" title="风险预算（相关性修正）" type="inner">
              <Descriptions column={1} size="small">
                <Descriptions.Item label="年化波动率">{formatPct(advice.risk_position.annualized_vol)}</Descriptions.Item>
                <Descriptions.Item label="近1年最大回撤">
                  <Text type="danger">{formatPct(advice.risk_position.max_drawdown_1y)}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="风险预算仓位">{formatPct(advice.risk_position.risk_budget_pct)}</Descriptions.Item>
                <Descriptions.Item label="参考仓位">{formatPct(advice.risk_position.suggested_position_pct)}</Descriptions.Item>
                <Descriptions.Item label="参考调整金额">¥{advice.risk_position.suggested_amount.toLocaleString()}</Descriptions.Item>
              </Descriptions>
            </Card>
          </Col>
        )}
      </Row>
      {advice.technical_indicators && (
        <Card size="small" title="技术指标" type="inner" style={{marginTop:12}}>
          <Space size={16} wrap>
            <Text>MA5: {advice.technical_indicators.ma5?.toFixed(4)||'-'}</Text>
            <Text>MA20: {advice.technical_indicators.ma20?.toFixed(4)||'-'}</Text>
            <Text>MACD: <Tag color={advice.technical_indicators.macd_signal==='bullish'?'red':advice.technical_indicators.macd_signal==='bearish'?'green':'default'}>{advice.technical_indicators.macd_signal}</Tag></Text>
            <Text>RSI(14): {advice.technical_indicators.rsi_14?.toFixed(1)||'-'}</Text>
            <Text>布林位置: {advice.technical_indicators.boll_position?.toFixed(2)||'-'}</Text>
            <Text>趋势: {(advice.technical_indicators.trend_score*100).toFixed(0)}</Text>
          </Space>
        </Card>
      )}
      {advice.limitations && advice.limitations.length > 0 && (
        <Collapse ghost style={{marginTop:8}}>
          <Panel header={<Text type="secondary" style={{fontSize:12}}>模型局限性（{advice.limitations.length}条）</Text>} key="lim">
            <List size="small" dataSource={advice.limitations} renderItem={item=><List.Item style={{padding:'2px 0'}}><Text type="secondary" style={{fontSize:11}}>• {item}</Text></List.Item>}/>
          </Panel>
        </Collapse>
      )}
    </>
  );
}
