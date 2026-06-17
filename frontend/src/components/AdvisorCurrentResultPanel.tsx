import { AdvisorAdviceDetail } from '@/components/AdvisorAdviceDetail';
import { AdvisorAdviceTable } from '@/components/AdvisorAdviceTable';
import { AdvisorCheckResultSummarySection } from '@/components/AdvisorCheckResultSummarySection';
import { AdvisorCurrentProfileSection } from '@/components/AdvisorCurrentProfileSection';
import { AdvisorDataTrustPanel } from '@/components/AdvisorDataTrustPanel';
import { AdvisorEmptyResultGuide } from '@/components/AdvisorEmptyResultGuide';
import { AdvisorReferenceActionsSection } from '@/components/AdvisorReferenceActionsSection';
import { AdvisorReasoningNotice } from '@/components/AdvisorReasoningNotice';
import { AdvisorResultSection as ResultSection } from '@/components/AdvisorResultSection';
import { AdvisorReviewAuditNotice } from '@/components/AdvisorReviewAuditNotice';
import type { AdvisorAnalyzeResponse, TradingAdviceItem } from '@/api/advisor';
import type { ColumnsType } from 'antd/es/table';
import type {
  AdvisorReminderCategory,
  AdvisorReminderCenterItem,
} from '@/components/AdvisorReminderCenter';
import type { AdvisorViewMode } from '@/utils/advisorPreferences';

export function AdvisorCurrentResultPanel({
  result,
  isLoading,
  viewMode,
  columns,
  reminderItems,
  enabledReminderCategories,
  fundTypeCount,
  saving,
  hasHighRiskAdvice,
  onChangeReminderCategories,
  onExportAudit,
  onSave,
}: {
  result: AdvisorAnalyzeResponse | null;
  isLoading: boolean;
  viewMode: AdvisorViewMode;
  columns: ColumnsType<TradingAdviceItem>;
  reminderItems: AdvisorReminderCenterItem[];
  enabledReminderCategories: AdvisorReminderCategory[];
  fundTypeCount?: number;
  saving: boolean;
  hasHighRiskAdvice: boolean;
  onChangeReminderCategories: (categories: AdvisorReminderCategory[]) => void;
  onExportAudit: () => void;
  onSave: () => void;
}) {
  if (!result && !isLoading) {
    return <AdvisorEmptyResultGuide fundTypeCount={fundTypeCount} />;
  }

  if (!result) return null;

  return (
    <>
      <AdvisorCurrentProfileSection viewMode={viewMode} userProfile={result.user_profile} />

      <AdvisorDataTrustPanel result={result} />

      <AdvisorCheckResultSummarySection
        result={result}
        reminderItems={reminderItems}
        enabledReminderCategories={enabledReminderCategories}
        onChangeReminderCategories={onChangeReminderCategories}
      />

      <ResultSection title="为什么这样判断">
        <AdvisorReasoningNotice tradingTime={result.trading_time} />
      </ResultSection>

      <AdvisorReferenceActionsSection
        saving={saving}
        hasHighRiskAdvice={hasHighRiskAdvice}
        onExportAudit={onExportAudit}
        onSave={onSave}
      >
        <AdvisorAdviceTable
          columns={columns}
          advices={result.advices}
          scrollX={viewMode === 'expert' ? 1300 : 1000}
          expandedRowRender={(record) => <AdvisorAdviceDetail advice={record} viewMode={viewMode} detail={null} />}
        />
      </AdvisorReferenceActionsSection>

      <ResultSection title="历史复盘与审计">
        <AdvisorReviewAuditNotice
          disclaimer={result.disclaimer}
          limitations={result.advices.length > 0 ? result.advices[0].limitations : []}
        />
      </ResultSection>
    </>
  );
}
