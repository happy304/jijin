import { Alert, Space, Tag, Typography } from 'antd';
import { AdvisorResultSection } from '@/components/AdvisorResultSection';
import {
  formatCurrency,
  investmentGoalLabel,
  investmentHorizonLabel,
  liquidityNeedLabel,
  riskLevelLabel,
  riskLevelTagColor,
  toleranceLabel,
} from '@/utils/advisorDisplay';

const { Text } = Typography;

type AdvisorViewMode = 'novice' | 'expert';

export function AdvisorCurrentProfileSection({
  viewMode,
  userProfile,
}: {
  viewMode: AdvisorViewMode;
  userProfile?: Record<string, unknown> | null;
}) {
  return (
    <AdvisorResultSection title="我的投资情况">
      <Alert
        type={viewMode === 'novice' ? 'info' : 'success'}
        showIcon
        style={{ marginBottom: 12 }}
        message={viewMode === 'novice' ? '当前为新手模式：优先看动作、金额、理由和风险提示。' : '当前为专家模式：展示完整评分、审计与专业诊断字段。'}
      />
      {userProfile && Object.keys(userProfile).length > 0 ? (
        <Space wrap>
          {userProfile.risk_level != null && <Tag color={riskLevelTagColor(String(userProfile.risk_level))}>风险偏好：{riskLevelLabel(String(userProfile.risk_level))}</Tag>}
          {userProfile.investment_goal != null && <Tag>目标：{investmentGoalLabel(String(userProfile.investment_goal))}</Tag>}
          {userProfile.investment_horizon != null && <Tag>期限：{investmentHorizonLabel(String(userProfile.investment_horizon))}</Tag>}
          {userProfile.liquidity_need != null && <Tag>流动性：{liquidityNeedLabel(String(userProfile.liquidity_need))}</Tag>}
          {userProfile.max_drawdown_tolerance != null && <Tag>最大回撤：{(Number(userProfile.max_drawdown_tolerance) * 100).toFixed(0)}%</Tag>}
          {userProfile.monthly_invest_amount != null && <Tag>月度预算：{formatCurrency(Number(userProfile.monthly_invest_amount))}</Tag>}
          {userProfile.industry_concentration_tolerance != null && <Tag>集中度容忍：{toleranceLabel(String(userProfile.industry_concentration_tolerance))}</Tag>}
          {userProfile.qdii_fx_risk_tolerance != null && <Tag>QDII汇率风险：{toleranceLabel(String(userProfile.qdii_fx_risk_tolerance))}</Tag>}
          {userProfile.fee_sensitivity != null && <Tag>费率敏感度：{toleranceLabel(String(userProfile.fee_sensitivity))}</Tag>}
        </Space>
      ) : (
        <Text type="secondary">本次未填写额外投资画像，系统按当前风险偏好与持仓生成组合检查。</Text>
      )}
    </AdvisorResultSection>
  );
}
