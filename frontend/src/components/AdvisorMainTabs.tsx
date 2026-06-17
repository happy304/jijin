import { HistoryOutlined, InfoCircleOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { Alert, Tabs } from 'antd';
import type { FormInstance } from 'antd';
import type { TabsProps } from 'antd';
import { AdvisorAdvancedCrossSectionalPanel, AdvisorEngineValidationPanel } from '@/components/AdvisorAdvancedResearchPanel';
import { AdvisorAnalyzePanel } from '@/components/AdvisorAnalyzePanel';
import { AdvisorCurrentResultPanel } from '@/components/AdvisorCurrentResultPanel';
import { AdvisorHistoryPanel } from '@/components/AdvisorHistoryPanel';
import type {
  AdvisorAnalyzeResponse,
  AdvisorHistoryDetailResponse,
  AdvisorHistoryItem,
  AdvisorPositionImportHistoryResponse,
  TradingAdviceItem,
} from '@/api/advisor';
import type { ColumnsType } from 'antd/es/table';
import type { AdvisorFundOption } from '@/utils/advisorFundOptions';
import type { FundOptionSummary } from '@/api/funds';
import type { AdvisorFavoriteGroup, AdvisorViewMode } from '@/utils/advisorPreferences';
import type { AdvisorPositionItem } from '@/utils/advisorPositions';
import type { AdvisorAnalyzeFormValues, AdvisorStrategyAnalyzeFormValues } from '@/utils/advisorRequestPayloads';
import type { AdvisorActiveReminderListItem } from '@/components/AdvisorActiveReminderListCard';
import type {
  AdvisorReminderCategory,
  AdvisorReminderCenterItem,
} from '@/components/AdvisorReminderCenter';

type AdvisorStrategyOption = { value: number; label: string };

interface AdvisorAnalyzeTabProps {
  activeTab: string;
  manualForm: FormInstance;
  strategyForm: FormInstance;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  recentFunds: string[];
  favoriteGroups: AdvisorFavoriteGroup[];
  hotFundCodes: string[];
  fundOptions: AdvisorFundOption[];
  strategyOptions: AdvisorStrategyOption[];
  positions: AdvisorPositionItem[];
  selectedFundCodes?: string[];
  selectedStrategyFundCodes?: string[];
  importHistoryData?: AdvisorPositionImportHistoryResponse;
  importHistoryLoading: boolean;
  syncingPositions: boolean;
  downloadingTemplate: boolean;
  importingPositions: boolean;
  restoringImportHistory: boolean;
  restoringImportId: number | null;
  result: AdvisorAnalyzeResponse | null;
  viewMode: AdvisorViewMode;
  columns: ColumnsType<TradingAdviceItem>;
  currentReminderItems: AdvisorReminderCenterItem[];
  enabledReminderCategories: AdvisorReminderCategory[];
  fundTypeCount?: number;
  savingResult: boolean;
  hasHighRiskAdvice: boolean;
  onChangeAnalyzeTab: (tab: string) => void;
  onManualAnalyze: (values: AdvisorAnalyzeFormValues) => void;
  onStrategyAnalyze: (values: AdvisorStrategyAnalyzeFormValues) => void;
  onPickFund: (code: string) => void;
  onApplyFavoriteGroup: (fundCodes: string[]) => void;
  onSaveCurrentSelection: () => void;
  onDownloadTemplate: (format: 'csv' | 'xlsx') => void;
  onImportPositions: (file: File) => Promise<boolean>;
  onImportHistoryPageChange: (page: number) => void;
  onRestorePositions: (importId: number) => void;
  onAddPosition: () => void;
  onRemovePosition: (index: number) => void;
  onUpdatePosition: (index: number, field: keyof AdvisorPositionItem, value: string | number) => void;
  onChangeReminderCategories: (categories: AdvisorReminderCategory[]) => void;
  onExportCurrentAudit: () => void;
  onSaveCurrentResult: () => void;
}

interface AdvisorHistoryTabProps {
  activeReminderItems: AdvisorActiveReminderListItem[];
  remindersLoading: boolean;
  remindersRefreshing: boolean;
  historyDetail?: AdvisorHistoryDetailResponse;
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
  onBackHistoryDetail: () => void;
  onRefreshHistory: (id: number) => void;
  onExportHistoryAudit: () => void;
  onLoadHistoryToForm: (detail: AdvisorHistoryDetailResponse) => void;
  onHistoryPageChange: (page: number) => void;
  onViewHistory: (item: AdvisorHistoryItem) => void;
  onDeleteHistory: (id: number) => void;
}

export interface AdvisorMainTabsProps extends AdvisorAnalyzeTabProps, AdvisorHistoryTabProps {
  pageTab: string;
  fundMap: Map<string, FundOptionSummary>;
  showAdvancedResearch: boolean;
  onChangePageTab: (tab: string) => void;
}

export function AdvisorMainTabs(props: AdvisorMainTabsProps) {
  const {
    pageTab,
    fundOptions,
    fundMap,
    showAdvancedResearch,
    onChangePageTab,
  } = props;

  const items = buildAdvisorMainTabItems({
    analyzeProps: props,
    historyProps: props,
    fundOptions,
    fundMap,
    showAdvancedResearch,
  });

  return (
    <Tabs
      className="page-tabs advisor-main-tabs"
      activeKey={pageTab}
      onChange={onChangePageTab}
      style={{ marginBottom: 16 }}
      items={items}
    />
  );
}

function buildAdvisorMainTabItems({
  analyzeProps,
  historyProps,
  fundOptions,
  fundMap,
  showAdvancedResearch,
}: {
  analyzeProps: AdvisorAnalyzeTabProps;
  historyProps: AdvisorHistoryTabProps;
  fundOptions: AdvisorFundOption[];
  fundMap: Map<string, FundOptionSummary>;
  showAdvancedResearch: boolean;
}): TabsProps['items'] {
  const baseItems: TabsProps['items'] = [
    {
      key: 'analyze',
      label: <span><ThunderboltOutlined /> 生成组合检查</span>,
      children: <AdvisorAnalyzeTabContent {...analyzeProps} />,
    },
    {
      key: 'history',
      label: <span><HistoryOutlined /> 历史记录</span>,
      children: <AdvisorHistoryTabContent {...historyProps} />,
    },
  ];

  if (!showAdvancedResearch) return baseItems;

  return [
    ...baseItems,
    {
      key: 'validate',
      label: <span><InfoCircleOutlined /> 引擎验证</span>,
      children: <AdvisorEngineValidationPanel fundOptions={fundOptions} fundMap={fundMap} />,
    },
    {
      key: 'cross-sectional',
      label: <span><ThunderboltOutlined /> 截面选基</span>,
      children: <AdvisorAdvancedCrossSectionalPanel />,
    },
  ];
}

function AdvisorAnalyzeTabContent({
  activeTab,
  manualForm,
  strategyForm,
  isLoading,
  isError,
  error,
  recentFunds,
  favoriteGroups,
  hotFundCodes,
  fundOptions,
  strategyOptions,
  positions,
  selectedFundCodes,
  selectedStrategyFundCodes,
  importHistoryData,
  importHistoryLoading,
  syncingPositions,
  downloadingTemplate,
  importingPositions,
  restoringImportHistory,
  restoringImportId,
  result,
  viewMode,
  columns,
  currentReminderItems,
  enabledReminderCategories,
  fundTypeCount,
  savingResult,
  hasHighRiskAdvice,
  onChangeAnalyzeTab,
  onManualAnalyze,
  onStrategyAnalyze,
  onPickFund,
  onApplyFavoriteGroup,
  onSaveCurrentSelection,
  onDownloadTemplate,
  onImportPositions,
  onImportHistoryPageChange,
  onRestorePositions,
  onAddPosition,
  onRemovePosition,
  onUpdatePosition,
  onChangeReminderCategories,
  onExportCurrentAudit,
  onSaveCurrentResult,
}: AdvisorAnalyzeTabProps) {
  return (
    <>
      <AdvisorAnalyzePanel
        activeTab={activeTab}
        manualForm={manualForm}
        strategyForm={strategyForm}
        loading={isLoading}
        recentFunds={recentFunds}
        favoriteGroups={favoriteGroups}
        hotFundCodes={hotFundCodes}
        fundOptions={fundOptions}
        strategyOptions={strategyOptions}
        positions={positions}
        selectedFundCodes={selectedFundCodes}
        selectedStrategyFundCodes={selectedStrategyFundCodes}
        importHistoryData={importHistoryData}
        importHistoryLoading={importHistoryLoading}
        syncingPositions={syncingPositions}
        downloadingTemplate={downloadingTemplate}
        importingPositions={importingPositions}
        restoringImportHistory={restoringImportHistory}
        restoringImportId={restoringImportId}
        onChangeTab={onChangeAnalyzeTab}
        onManualAnalyze={onManualAnalyze}
        onStrategyAnalyze={onStrategyAnalyze}
        onPickFund={onPickFund}
        onApplyFavoriteGroup={onApplyFavoriteGroup}
        onSaveCurrentSelection={onSaveCurrentSelection}
        onDownloadTemplate={onDownloadTemplate}
        onImportPositions={onImportPositions}
        onImportHistoryPageChange={onImportHistoryPageChange}
        onRestorePositions={onRestorePositions}
        onAddPosition={onAddPosition}
        onRemovePosition={onRemovePosition}
        onUpdatePosition={onUpdatePosition}
      />

      {isError && (
        <Alert
          type="error"
          message="分析失败"
          description={error instanceof Error ? error.message : '请检查参数'}
          showIcon
          closable
          style={{ marginBottom: 16 }}
        />
      )}

      <AdvisorCurrentResultPanel
        result={result}
        isLoading={isLoading}
        viewMode={viewMode}
        columns={columns}
        reminderItems={currentReminderItems}
        enabledReminderCategories={enabledReminderCategories}
        fundTypeCount={fundTypeCount}
        saving={savingResult}
        hasHighRiskAdvice={hasHighRiskAdvice}
        onChangeReminderCategories={onChangeReminderCategories}
        onExportAudit={onExportCurrentAudit}
        onSave={onSaveCurrentResult}
      />
    </>
  );
}

function AdvisorHistoryTabContent({
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
  onBackHistoryDetail,
  onRefreshHistory,
  onExportHistoryAudit,
  onLoadHistoryToForm,
  onHistoryPageChange,
  onViewHistory,
  onDeleteHistory,
}: AdvisorHistoryTabProps) {
  return (
    <AdvisorHistoryPanel
      activeReminderItems={activeReminderItems}
      remindersLoading={remindersLoading}
      remindersRefreshing={remindersRefreshing}
      historyDetail={historyDetail}
      viewingId={viewingId}
      viewMode={viewMode}
      columns={columns}
      historyReminderItems={historyReminderItems}
      enabledReminderCategories={enabledReminderCategories}
      historyItems={historyItems}
      historyTotal={historyTotal}
      historyLoading={historyLoading}
      historyPage={historyPage}
      historyRefreshing={historyRefreshing}
      historyRefreshingId={historyRefreshingId}
      onRefreshReminders={onRefreshReminders}
      onOpenReminder={onOpenReminder}
      onDismissReminder={onDismissReminder}
      onChangeReminderCategories={onChangeReminderCategories}
      onBackDetail={onBackHistoryDetail}
      onRefreshHistory={onRefreshHistory}
      onExportHistoryAudit={onExportHistoryAudit}
      onLoadHistoryToForm={onLoadHistoryToForm}
      onHistoryPageChange={onHistoryPageChange}
      onViewHistory={onViewHistory}
      onDeleteHistory={onDeleteHistory}
    />
  );
}
