import { Alert, Space, Tag } from 'antd';
import {
  formatCurrency,
  investmentGoalLabel,
  investmentHorizonLabel,
  liquidityNeedLabel,
  riskLevelLabel,
  riskLevelTagColor,
  toleranceLabel,
} from '@/utils/advisorDisplay';

export function AdvisorUserProfileSnapshot({
  userProfile,
}: {
  userProfile?: Record<string, unknown> | null;
}) {
  if (!userProfile || Object.keys(userProfile).length === 0) return null;

  return (
    <Alert
      type="info"
      showIcon
      message="当时投资画像"
      description={(
        <Space wrap>
          {userProfile.risk_level != null && <Tag color={riskLevelTagColor(String(userProfile.risk_level))}>风险偏好：{riskLevelLabel(String(userProfile.risk_level))}</Tag>}
          {userProfile.investment_goal != null && <Tag>目标：{investmentGoalLabel(String(userProfile.investment_goal))}</Tag>}
          {userProfile.investment_horizon != null && <Tag>期限：{investmentHorizonLabel(String(userProfile.investment_horizon))}</Tag>}
          {userProfile.liquidity_need != null && <Tag>流动性：{liquidityNeedLabel(String(userProfile.liquidity_need))}</Tag>}
          {userProfile.max_drawdown_tolerance != null && <Tag>回撤承受：{(Number(userProfile.max_drawdown_tolerance) * 100).toFixed(0)}%</Tag>}
          {userProfile.monthly_invest_amount != null && <Tag>月度预算：{formatCurrency(Number(userProfile.monthly_invest_amount))}</Tag>}
          {userProfile.industry_concentration_tolerance != null && <Tag>集中度容忍：{toleranceLabel(String(userProfile.industry_concentration_tolerance))}</Tag>}
          {userProfile.qdii_fx_risk_tolerance != null && <Tag>QDII汇率风险：{toleranceLabel(String(userProfile.qdii_fx_risk_tolerance))}</Tag>}
          {userProfile.fee_sensitivity != null && <Tag>费率敏感度：{toleranceLabel(String(userProfile.fee_sensitivity))}</Tag>}
        </Space>
      )}
      style={{ marginBottom: 16 }}
    />
  );
}
