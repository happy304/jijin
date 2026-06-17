import { AdvisorActiveReminderListCard, type AdvisorActiveReminderListItem } from '@/components/AdvisorActiveReminderListCard';
import { AdvisorHistoryDetailPanel } from '@/components/AdvisorHistoryDetailPanel';
import { AdvisorHistoryListCard } from '@/components/AdvisorHistoryListCard';
import type { AdvisorHistoryDetailResponse, AdvisorHistoryItem, TradingAdviceItem } from '@/api/advisor';
import type { ColumnsType } from 'antd/es/table';
import type {
  AdvisorReminderCategory,
  AdvisorReminderCenterItem,
} from '@/components/AdvisorReminderCenter';
import type { AdvisorViewMode } from '@/utils/advisorPreferences';

export function AdvisorHistoryPanel({
  activeReminderItems,
  remindersLoading,
  remindersRefreshing,
  historyDetail,
  viewingId,
  viewMode,
  columns,
  historyReminderItems,
  enabledReminderCategories,
  historyItems,
  historyTotal,
  historyLoading,
  historyPage,
  historyRefreshing,
  historyRefreshingId,
  onRefreshReminders,
  onOpenReminder,
  onDismissReminder,
  onChangeReminderCategories,
  onBackDetail,
  onRefreshHistory,
  onExportHistoryAudit,
  onLoadHistoryToForm,
  onHistoryPageChange,
  onViewHistory,
  onDeleteHistory,
}: {
  activeReminderItems: AdvisorActiveReminderListItem[];
  remindersLoading: boolean;
  remindersRefreshing: boolean;
  historyDetail: AdvisorHistoryDetailResponse | undefined;
  viewingId: number | null;
  viewMode: AdvisorViewMode;
  columns: ColumnsType<TradingAdviceItem>;
  historyReminderItems: AdvisorReminderCenterItem[];
  enabledReminderCategories: AdvisorReminderCategory[];
  historyItems: AdvisorHistoryItem[];
  historyTotal: number;
  historyLoading: boolean;
  historyPage: number;
  historyRefreshing: boolean;
  historyRefreshingId: number | null;
  onRefreshReminders: () => void;
  onOpenReminder: (advisorResultId: number) => void;
  onDismissReminder: (reminderId: number) => void;
  onChangeReminderCategories: (categories: AdvisorReminderCategory[]) => void;
  onBackDetail: () => void;
  onRefreshHistory: (id: number) => void;
  onExportHistoryAudit: () => void;
  onLoadHistoryToForm: (detail: AdvisorHistoryDetailResponse) => void;
  onHistoryPageChange: (page: number) => void;
  onViewHistory: (item: AdvisorHistoryItem) => void;
  onDeleteHistory: (id: number) => void;
}) {
  return (
    <>
      {/* 主动提醒订阅/推送为个人自用场景暂不启用；保留普通提醒列表与手动刷新即可。 */}
      <AdvisorActiveReminderListCard
        items={activeReminderItems}
        loading={remindersLoading}
        refreshing={remindersRefreshing}
        onRefresh={onRefreshReminders}
        onOpen={onOpenReminder}
        onDismiss={onDismissReminder}
      />
      {viewingId && historyDetail ? (
        <AdvisorHistoryDetailPanel
          detail={historyDetail}
          resultId={viewingId}
          viewMode={viewMode}
          columns={columns}
          reminderItems={historyReminderItems}
          enabledReminderCategories={enabledReminderCategories}
          refreshing={historyRefreshing}
          onChangeReminderCategories={onChangeReminderCategories}
          onBack={onBackDetail}
          onRefresh={() => onRefreshHistory(historyDetail.id)}
          onExportAudit={onExportHistoryAudit}
          onLoadToForm={() => onLoadHistoryToForm(historyDetail)}
        />
      ) : (
        <AdvisorHistoryListCard
          items={historyItems}
          total={historyTotal}
          loading={historyLoading}
          currentPage={historyPage}
          refreshing={historyRefreshing}
          refreshingId={historyRefreshingId}
          onPageChange={onHistoryPageChange}
          onView={onViewHistory}
          onRefresh={onRefreshHistory}
          onDelete={onDeleteHistory}
        />
      )}
    </>
  );
}
