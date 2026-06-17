import { Alert, Col, List, Row, Space, Tag, Typography } from 'antd';
import type { TradingAdviceItem } from '@/api/advisor';
import { dataQualityStatusLabel } from '@/components/DataTrustNotice';
import {
  formatAuditValue,
  formatMaybePct,
  overfitRiskLabel,
} from '@/utils/advisorDisplay';

const { Text } = Typography;

export function AdvisorQualityRiskAlerts({ advice }: { advice: TradingAdviceItem }) {
  if (!advice.data_quality && !advice.overfit_risk && !advice.risk_constraints) return null;

  return (
    <Row gutter={12} style={{ marginBottom: 12 }}>
      {advice.data_quality && (
        <Col span={12}>
          <Alert
            type={advice.data_quality.status === 'poor' ? 'error' : advice.data_quality.status === 'warning' ? 'warning' : 'success'}
            showIcon
            message={`数据质量：${dataQualityStatusLabel(advice.data_quality.status)}`}
            description={
              <div>
                <Space size={6} wrap>
                  <Tag>质量分 {Math.round((advice.data_quality.score || 0) * 100)}</Tag>
                  <Tag>覆盖率 {formatMaybePct(advice.data_quality.coverage_ratio)}</Tag>
                  <Tag>净值样本 {advice.data_quality.nav_count}</Tag>
                  <Tag>最大缺失 {advice.data_quality.max_gap_days || 0}天</Tag>
                  <Tag>跳变 {advice.data_quality.spike_count || 0}次</Tag>
                  {advice.data_quality.freshness_days != null && <Tag>新鲜度 {advice.data_quality.freshness_days}天</Tag>}
                  {advice.data_quality.source_consistency && (
                    <Tag>来源 {advice.data_quality.source_consistency.source_count || 0}个 / 主来源 {advice.data_quality.source_consistency.primary_source || '未知'}</Tag>
                  )}
                  {advice.data_quality.source_consistency && (advice.data_quality.source_consistency.source_switch_count || 0) > 0 && (
                    <Tag color="orange">来源切换 {advice.data_quality.source_consistency.source_switch_count}次</Tag>
                  )}
                  {advice.data_quality.adjustment_consistency && (
                    <Tag>复权覆盖 {formatMaybePct(advice.data_quality.adjustment_consistency.adjusted_coverage_ratio)}</Tag>
                  )}
                  {advice.data_quality.adjustment_consistency && (advice.data_quality.adjustment_consistency.fallback_to_unit_count || 0) > 0 && (
                    <Tag color="orange">单位净值回退 {advice.data_quality.adjustment_consistency.fallback_to_unit_count}条</Tag>
                  )}
                  {advice.data_quality.adjustment_consistency && (advice.data_quality.adjustment_consistency.factor_jump_count || 0) > 0 && (
                    <Tag color="red">复权异常 {advice.data_quality.adjustment_consistency.factor_jump_count}次</Tag>
                  )}
                </Space>
                {(advice.data_quality.warnings || []).length > 0 && (
                  <List size="small" dataSource={advice.data_quality.warnings.slice(0, 3)} renderItem={(item)=><List.Item style={{ padding: '2px 0' }}><Text type="secondary" style={{ fontSize: 12 }}>• {item}</Text></List.Item>} />
                )}
              </div>
            }
          />
        </Col>
      )}
      {advice.overfit_risk && (
        <Col span={12}>
          <Alert
            type={advice.overfit_risk.level === 'high' ? 'error' : advice.overfit_risk.level === 'medium' ? 'warning' : 'success'}
            showIcon
            message={`过拟合风险：${overfitRiskLabel(advice.overfit_risk.level)}`}
            description={
              <div>
                <Space size={6} wrap>
                  <Tag>风险分 {Math.round((advice.overfit_risk.score || 0) * 100)}</Tag>
                  <Tag>门禁 {advice.overfit_risk.gate_action}</Tag>
                  <Tag>OOS信号 {advice.overfit_risk.oos_signal_count}</Tag>
                  {advice.overfit_risk.oos_ic != null && <Tag>OOS IC {formatAuditValue(advice.overfit_risk.oos_ic)}</Tag>}
                  {advice.overfit_risk.ic_degradation != null && <Tag>IC衰减 {formatAuditValue(advice.overfit_risk.ic_degradation)}</Tag>}
                  {advice.overfit_risk.pbo != null && <Tag color={advice.overfit_risk.pbo >= 0.5 ? 'red' : 'blue'}>PBO {formatMaybePct(advice.overfit_risk.pbo)}</Tag>}
                  {advice.overfit_risk.cpcv_n_paths != null && advice.overfit_risk.cpcv_n_paths > 0 && <Tag>CPCV路径 {advice.overfit_risk.cpcv_n_paths}</Tag>}
                  {advice.overfit_risk.cpcv_avg_oos_sharpe != null && <Tag>OOS Sharpe {formatAuditValue(advice.overfit_risk.cpcv_avg_oos_sharpe)}</Tag>}
                </Space>
                {(advice.overfit_risk.reasons || []).length > 0 && (
                  <List size="small" dataSource={advice.overfit_risk.reasons.slice(0, 3)} renderItem={(item)=><List.Item style={{ padding: '2px 0' }}><Text type="secondary" style={{ fontSize: 12 }}>• {item}</Text></List.Item>} />
                )}
              </div>
            }
          />
        </Col>
      )}
      {advice.risk_constraints && (
        <Col span={12}>
          <Alert
            type={advice.risk_constraints.status === 'blocked' ? 'error' : advice.risk_constraints.status === 'adjusted' ? 'warning' : 'success'}
            showIcon
            message={`风控约束：${advice.risk_constraints.status === 'blocked' ? '已阻断' : advice.risk_constraints.status === 'adjusted' ? '已调整' : '通过'}`}
            description={
              <div>
                <Space size={6} wrap>
                  <Tag>原建议 {advice.risk_constraints.original_suggested_amount.toFixed(2)}</Tag>
                  <Tag color={advice.risk_constraints.status === 'passed' ? 'green' : 'orange'}>调整后 {advice.risk_constraints.adjusted_suggested_amount.toFixed(2)}</Tag>
                  {Object.entries(advice.risk_constraints.constraints || {}).map(([key, value]) => (
                    <Tag key={key}>{key} {formatMaybePct(value)}</Tag>
                  ))}
                </Space>
                {(advice.risk_constraints.violations || []).length > 0 && (
                  <List size="small" dataSource={advice.risk_constraints.violations.slice(0, 4)} renderItem={(item)=><List.Item style={{ padding: '2px 0' }}><Text type={item.severity === 'high' ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>• {item.message}</Text></List.Item>} />
                )}
              </div>
            }
          />
        </Col>
      )}
    </Row>
  );
}
