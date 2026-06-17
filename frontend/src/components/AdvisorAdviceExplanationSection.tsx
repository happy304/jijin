import { Alert, Card, List, Space, Tag, Typography } from 'antd';
import type { TradingAdviceItem } from '@/api/advisor';
import {
  formatAuditValue,
  formatMaybePct,
  oosSelectionSourceLabel,
  reliabilityStatusLabel,
} from '@/utils/advisorDisplay';

const { Text } = Typography;

export function AdvisorAdviceExplanationSection({ advice }: { advice: TradingAdviceItem }) {
  return (
    <>
      {(advice.decision_support_only || advice.not_investment_advice_disclaimer) && (
        <Alert
          type="info"
          showIcon
          message="个人决策支持"
          description={advice.not_investment_advice_disclaimer || '仅供复核与观察，不构成投资建议或收益承诺。'}
          style={{ marginBottom: 12 }}
        />
      )}
      {advice.reasoning && (
        <Card size="small" title="检查结果解释" type="inner" style={{ marginBottom: 12 }}>
          <Alert
            type={advice.reasoning.confidence_level === 'low' ? 'warning' : 'info'}
            message={advice.reasoning.summary}
            showIcon
            style={{ marginBottom: 12 }}
          />
          <List
            size="small"
            dataSource={advice.reasoning.factors}
            renderItem={(factor) => (
              <List.Item style={{ padding: '4px 0' }}>
                <Space align="start">
                  <Tag color={factor.impact === 'positive' ? 'red' : factor.impact === 'negative' ? 'green' : 'default'}>{factor.name}</Tag>
                  <Text style={{ fontSize: 12 }}>{factor.explanation}</Text>
                  {factor.score != null && <Text type="secondary" style={{ fontSize: 12 }}>({(factor.score * 100).toFixed(1)})</Text>}
                </Space>
              </List.Item>
            )}
          />
        </Card>
      )}
      {advice.suitability && (
        <Alert
          type={advice.suitability.matched ? 'success' : 'warning'}
          showIcon
          message={advice.suitability.matched ? '风险等级匹配' : '风险等级不匹配'}
          description={advice.suitability.warning || `用户风险偏好：${advice.suitability.user_risk_level}，基金估算风险等级：${advice.suitability.fund_risk_level}`}
          style={{ marginBottom: 12 }}
        />
      )}
      {advice.reliability_adjustment && (
        <Alert
          type={advice.reliability_adjustment.status === 'healthy' ? 'success' : advice.reliability_adjustment.status === 'unhealthy' ? 'error' : 'warning'}
          showIcon
          message={`防过拟合可靠性调整：${reliabilityStatusLabel(advice.reliability_adjustment.status)}`}
          description={
            <div>
              <div>{advice.reliability_adjustment.reason || '根据历史跟踪和引擎健康度对信号可靠性进行折扣'}</div>
              {advice.reliability_adjustment.metrics.oos_selection_source != null && (
                <div style={{ marginTop: 6 }}>
                  当前样本外缓存来源：{oosSelectionSourceLabel(String(advice.reliability_adjustment.metrics.oos_selection_source))}
                  {advice.reliability_adjustment.metrics.oos_requested_risk_level != null && `（请求风险档：${formatAuditValue(advice.reliability_adjustment.metrics.oos_requested_risk_level)}）`}
                </div>
              )}
              <Space size={6} wrap style={{ marginTop: 6 }}>
                <Tag>评分×{(advice.reliability_adjustment.multiplier * 100).toFixed(0)}%</Tag>
                <Tag>置信度×{(advice.reliability_adjustment.confidence_multiplier * 100).toFixed(0)}%</Tag>
                <Tag>金额×{(advice.reliability_adjustment.amount_multiplier * 100).toFixed(0)}%</Tag>
                {advice.reliability_adjustment.metrics.rolling_ic_20d != null && <Tag>IC: {formatAuditValue(advice.reliability_adjustment.metrics.rolling_ic_20d)}</Tag>}
                {advice.reliability_adjustment.metrics.rolling_ic_samples != null && <Tag>样本: {formatAuditValue(advice.reliability_adjustment.metrics.rolling_ic_samples)}</Tag>}
                {advice.reliability_adjustment.metrics.ic_trend != null && <Tag>趋势: {formatAuditValue(advice.reliability_adjustment.metrics.ic_trend)}</Tag>}
                {advice.reliability_adjustment.metrics.oos_risk_level != null && <Tag color="purple">实际OOS风险档: {formatAuditValue(advice.reliability_adjustment.metrics.oos_risk_level)}</Tag>}
                {advice.reliability_adjustment.metrics.oos_avg_ic != null && <Tag color="blue">OOS IC: {formatAuditValue(advice.reliability_adjustment.metrics.oos_avg_ic)}</Tag>}
                {advice.reliability_adjustment.metrics.oos_ic_degradation != null && <Tag color="orange">IC衰减: {formatAuditValue(advice.reliability_adjustment.metrics.oos_ic_degradation)}</Tag>}
                {advice.reliability_adjustment.metrics.oos_total_signals != null && <Tag>OOS信号: {formatAuditValue(advice.reliability_adjustment.metrics.oos_total_signals)}</Tag>}
                {advice.reliability_adjustment.metrics.oos_buy_hit_rate != null && <Tag>OOS增配关注命中: {formatMaybePct(advice.reliability_adjustment.metrics.oos_buy_hit_rate)}</Tag>}
                {advice.reliability_adjustment.metrics.oos_sell_hit_rate != null && <Tag>OOS减配关注命中: {formatMaybePct(advice.reliability_adjustment.metrics.oos_sell_hit_rate)}</Tag>}
                {advice.reliability_adjustment.metrics.oos_pbo != null && <Tag color={Number(advice.reliability_adjustment.metrics.oos_pbo) >= 0.5 ? 'red' : 'blue'}>PBO: {formatMaybePct(advice.reliability_adjustment.metrics.oos_pbo)}</Tag>}
                {advice.reliability_adjustment.metrics.oos_cpcv_n_paths != null && <Tag>CPCV路径: {formatAuditValue(advice.reliability_adjustment.metrics.oos_cpcv_n_paths)}</Tag>}
                {advice.reliability_adjustment.metrics.oos_snapshot_date != null && <Tag>OOS快照: {formatAuditValue(advice.reliability_adjustment.metrics.oos_snapshot_date)}</Tag>}
                {advice.reliability_adjustment.metrics.oos_updated_at != null && <Tag>OOS更新: {formatAuditValue(advice.reliability_adjustment.metrics.oos_updated_at)}</Tag>}
              </Space>
            </div>
          }
          style={{ marginBottom: 12 }}
        />
      )}
    </>
  );
}
