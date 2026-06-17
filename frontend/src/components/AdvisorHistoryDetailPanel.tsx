import { Alert } from 'antd';
import { AdvisorAdviceDetail } from '@/components/AdvisorAdviceDetail';
import { AdvisorAdviceTable } from '@/components/AdvisorAdviceTable';
import { AdvisorDataTrustPanel } from '@/components/AdvisorDataTrustPanel';
import { AdvisorExecutionAuditCard as ExecutionAuditCard } from '@/components/AdvisorExecutionAuditCard';
import { AdvisorExecutionRecordsCard } from '@/components/AdvisorExecutionRecordsCard';
import { AdvisorHistoricalPositionsCard } from '@/components/AdvisorHistoricalPositionsCard';
import { AdvisorHistoryDetailActions } from '@/components/AdvisorHistoryDetailActions';
import { AdvisorHistoryDetailSummaryCard } from '@/components/AdvisorHistoryDetailSummaryCard';
import { AdvisorNavDataWarnings } from '@/components/AdvisorNavDataWarnings';
import { AdvisorPerformanceCard as PerformanceCard } from '@/components/AdvisorPerformanceCard';
import { AdvisorReminderCenter as ReminderCenter } from '@/components/AdvisorReminderCenter';
import { AdvisorRiskComparisonSection as RiskComparisonSection } from '@/components/AdvisorRiskComparisonSection';
import { AdvisorUserProfileSnapshot } from '@/components/AdvisorUserProfileSnapshot';
import type { AdvisorHistoryDetailResponse, TradingAdviceItem } from '@/api/advisor';
import type { ColumnsType } from 'antd/es/table';
import type {
  AdvisorReminderCategory,
  AdvisorReminderCenterItem,
} from '@/components/AdvisorReminderCenter';
import type { AdvisorViewMode } from '@/utils/advisorPreferences';

export function AdvisorHistoryDetailPanel({
  detail,
  resultId,
  viewMode,
  columns,
  reminderItems,
  enabledReminderCategories,
  refreshing,
  onChangeReminderCategories,
  onBack,
  onRefresh,
  onExportAudit,
  onLoadToForm,
}: {
  detail: AdvisorHistoryDetailResponse;
  resultId: number;
  viewMode: AdvisorViewMode;
  columns: ColumnsType<TradingAdviceItem>;
  reminderItems: AdvisorReminderCenterItem[];
  enabledReminderCategories: AdvisorReminderCategory[];
  refreshing: boolean;
  onChangeReminderCategories: (categories: AdvisorReminderCategory[]) => void;
  onBack: () => void;
  onRefresh: () => void;
  onExportAudit: () => void;
  onLoadToForm: () => void;
}) {
  return (
    <div>
      <AdvisorHistoryDetailActions
        refreshing={refreshing}
        onBack={onBack}
        onRefresh={onRefresh}
        onExportAudit={onExportAudit}
        onLoadToForm={onLoadToForm}
      />
      <AdvisorNavDataWarnings
        navDataStale={detail.nav_data_stale}
        navQualityWarning={detail.nav_quality_warning}
      />
      <AdvisorHistoryDetailSummaryCard detail={detail}>
        <div style={{ marginBottom: 16 }}>
          <ReminderCenter
            items={reminderItems}
            title="历史提醒"
            enabledCategories={enabledReminderCategories}
            onChangeCategories={onChangeReminderCategories}
          />
        </div>
        {detail.execution_context && (
          <ExecutionAuditCard context={detail.execution_context} viewMode={viewMode} />
        )}
        <AdvisorUserProfileSnapshot userProfile={detail.user_profile} />
        <RiskComparisonSection comparison={detail.risk_comparison} />
        <AdvisorDataTrustPanel result={detail} />
        <AdvisorAdviceTable
          columns={columns}
          advices={detail.advices}
          scrollX={viewMode === 'expert' ? 1300 : 1000}
          expandedRowRender={(record) => <AdvisorAdviceDetail advice={record} viewMode={viewMode} detail={detail} />}
        />
      </AdvisorHistoryDetailSummaryCard>

      <AdvisorExecutionRecordsCard detail={detail} />

      <PerformanceCard resultId={resultId} />

      {detail.note && <Alert message="备注" description={detail.note} type="info" showIcon style={{ marginBottom: 16 }} />}
      <AdvisorHistoricalPositionsCard positionsDetail={detail.positions_detail} />
    </div>
  );
}
