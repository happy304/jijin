import type { AdvisorHistoryDetailResponse, TradingAdviceItem } from '@/api/advisor';
import { AdvisorAdviceExplanationSection } from '@/components/AdvisorAdviceExplanationSection';
import { AdvisorAdviceOverviewSection } from '@/components/AdvisorAdviceOverviewSection';
import { AdvisorDecisionAuditCard } from '@/components/AdvisorDecisionAuditCard';
import { AdvisorExpertAnalysisSection } from '@/components/AdvisorExpertAnalysisSection';
import { AdvisorLimitationsNotice } from '@/components/AdvisorLimitationsNotice';
import { AdvisorProfileConstraintsCard } from '@/components/AdvisorProfileConstraintsCard';
import { AdvisorQualityRiskAlerts } from '@/components/AdvisorQualityRiskAlerts';
import { AdvisorTradePlanImpactSection } from '@/components/AdvisorTradePlanImpactSection';
import { AdvisorTradeTimingCard } from '@/components/AdvisorTradeTimingCard';
import { AdvisorValidityRiskNotice } from '@/components/AdvisorValidityRiskNotice';
import { getExecutionPlanTasks } from '@/components/AdvisorExecutionRecordsCard';

type AdvisorViewMode = 'novice' | 'expert';

interface AdvisorAdviceDetailProps {
  advice: TradingAdviceItem;
  viewMode: AdvisorViewMode;
  detail?: AdvisorHistoryDetailResponse | null;
}

export function AdvisorAdviceDetail({ advice, viewMode, detail }: AdvisorAdviceDetailProps) {
  return (
    <div style={{ padding: '8px 16px' }}>
      <AdvisorAdviceOverviewSection advice={advice} viewMode={viewMode} />
      {advice.trade_timing && <AdvisorTradeTimingCard timing={advice.trade_timing} />}
      <AdvisorAdviceExplanationSection advice={advice} />
      <AdvisorQualityRiskAlerts advice={advice} />
      {viewMode === 'expert' && <AdvisorDecisionAuditCard advice={advice} />}
      <AdvisorProfileConstraintsCard advice={advice} />
      <AdvisorTradePlanImpactSection advice={advice} executionPlanTasks={getExecutionPlanTasks(advice, detail)} />
      <AdvisorValidityRiskNotice advice={advice} />
      {viewMode === 'expert' && <AdvisorExpertAnalysisSection advice={advice} />}
      {viewMode !== 'expert' && <AdvisorLimitationsNotice limitations={advice.limitations} />}
    </div>
  );
}
