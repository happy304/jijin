import { Divider } from 'antd';
import { AdvisorAdvancedResearchNotice } from '@/components/AdvisorAdvancedResearchNotice';
import { AdvisorBacktestPanel } from '@/components/AdvisorBacktestPanel';
import { AdvisorCrossSectionalPanel } from '@/components/AdvisorCrossSectionalPanel';
import { AdvisorEngineHealthCard } from '@/components/AdvisorEngineHealthCard';
import { AdvisorOOSStatusCard } from '@/components/AdvisorOOSStatusCard';
import { AdvisorWalkForwardPanel } from '@/components/AdvisorWalkForwardPanel';
import type { AdvisorFundOption } from '@/utils/advisorFundOptions';
import type { FundOptionSummary } from '@/api/funds';

export function AdvisorEngineValidationPanel({
  fundOptions,
  fundMap,
}: {
  fundOptions: AdvisorFundOption[];
  fundMap: Map<string, FundOptionSummary>;
}) {
  return (
    <div>
      <AdvisorAdvancedResearchNotice />
      <AdvisorEngineHealthCard />
      <AdvisorOOSStatusCard />
      <Divider />
      <AdvisorBacktestPanel fundOptions={fundOptions} fundMap={fundMap} />
      <Divider />
      <AdvisorWalkForwardPanel fundOptions={fundOptions} fundMap={fundMap} />
    </div>
  );
}

export function AdvisorAdvancedCrossSectionalPanel() {
  return <AdvisorCrossSectionalPanel />;
}
