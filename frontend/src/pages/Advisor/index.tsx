import { useState, useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import {
  Button,
  Form,
  message,
  Modal,
} from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import {
  useAnalyzeFunds,
  usePortfolioAdvice,
  useAdvisorConfig,
  useAdvisorHistory,
  useAdvisorHistoryDetail,
  // 主动提醒订阅/推送为个人自用场景暂不启用；相关 API hook 保留在 '@/api/advisor'，页面不再加载。
  // useAdvisorReminderPreference,
  // useUpdateAdvisorReminderPreference,
  // useCreateAdvisorReminderDigest,
  useAdvisorPositions,
  useAdvisorPositionImportHistory,
  useDownloadAdvisorPositionsTemplate,
  useRestoreAdvisorPositionsFromImportHistory,
  useReplaceAdvisorPositions,
  useImportAdvisorPositions,
  type AdvisorAnalyzeResponse,
  // 主动提醒订阅/推送暂不启用；类型保留在 API 层，需要恢复 UI 时再引入。
  // type AdvisorReminderPreference,
  // type AdvisorReminderPreferenceRequest,
} from '@/api/advisor';
import { useStrategyList } from '@/api/strategies';
import { useFundOptions } from '@/api/funds';
import { buildAdvisorAdviceColumns } from '@/components/AdvisorAdviceColumns';
import { AdvisorMainTabs } from '@/components/AdvisorMainTabs';
import { AdvisorPageHeader } from '@/components/AdvisorPageHeader';
import { AdvisorPositionImportFailureContent } from '@/components/AdvisorPositionImportFailureContent';
import { PageHero } from '@/components/PageHero';
import { StatCard } from '@/components/StatCard';
import type { AdvisorReminderCategory as ReminderCategory } from '@/components/AdvisorReminderCenter';
import { useFeatureProfile } from '@/api/settings';
import { BulbOutlined, HeartOutlined, StarOutlined, TeamOutlined } from '@ant-design/icons';
import { useAdvisorAuditExportHandlers } from '@/hooks/useAdvisorAuditExportHandlers';
import { useAdvisorFundShortcuts } from '@/hooks/useAdvisorFundShortcuts';
import { useAdvisorHistoryActions } from '@/hooks/useAdvisorHistoryActions';
import { useAdvisorPositionEditorActions } from '@/hooks/useAdvisorPositionEditorActions';
import { useAdvisorPositionsSync } from '@/hooks/useAdvisorPositionsSync';
import { useAdvisorReminderInbox } from '@/hooks/useAdvisorReminderInbox';
import { useAdvisorSaveResult } from '@/hooks/useAdvisorSaveResult';
import { downloadBlobFile } from '@/utils/fileDownload';
import {
  buildAdvisorHotFundCodes,
  buildAdvisorStrategyOptions,
  collectAdvisorExtraFundCodes,
  extractStrategyFundCodes,
} from '@/utils/advisorDerivedOptions';
import { buildAdvisorFundOptions } from '@/utils/advisorFundOptions';
import {
  buildAdviceReminders,
  buildHistoryReminders,
} from '@/utils/advisorReminderBuilders';
import {
  buildAdvisorAnalyzeRequest,
  buildAdvisorPortfolioRequest,
  buildManualLastRequestMeta,
  buildStrategyLastRequestMeta,
  type AdvisorAnalyzeFormValues,
  type AdvisorLastRequestMeta,
  type AdvisorStrategyAnalyzeFormValues,
} from '@/utils/advisorRequestPayloads';
import {
  invalidateAdvisorPositionImportHistoryQueries,
  invalidateAdvisorPositionQueries,
} from '@/utils/advisorQueryInvalidation';
import {
  loadSavedAdvisorPositions,
  normalizeAdvisorPositionItem,
  saveAdvisorPositions,
  type AdvisorPositionItem,
} from '@/utils/advisorPositions';
import {
  loadAdvisorViewMode,
  loadFavoriteGroups,
  loadRecentFunds,
  loadReminderPrefs,
  saveAdvisorViewMode,
  saveFavoriteGroups,
  saveRecentFunds,
  saveReminderPrefs,
  type AdvisorViewMode,
} from '@/utils/advisorPreferences';
import {
  buildAdvisorPositionImportModalTitle,
  buildAdvisorPositionImportSuccessMessage,
  buildAdvisorPositionRestoreSuccessMessage,
  getFailedAdvisorPositionImportRows,
  shouldShowAdvisorPositionImportReview,
} from '@/utils/advisorPositionImportResult';

/*
 * 主动提醒订阅/推送暂不启用（个人自用场景）。
 * 相关后端 API 与前端 API hook 保留，后续如需要多端推送，可恢复偏好设置组件并重新挂到历史页。
 */

export function AdvisorPage() {
  const [activeTab, setActiveTab] = useState<string>('manual');
  const [pageTab, setPageTab] = useState<string>('analyze');
  const [result, setResult] = useState<AdvisorAnalyzeResponse | null>(null);
  const [positions, setPositions] = useState<AdvisorPositionItem[]>(loadSavedAdvisorPositions);
  const [advisorViewMode, setAdvisorViewMode] = useState<AdvisorViewMode>(loadAdvisorViewMode);
  const [recentFunds, setRecentFunds] = useState<string[]>(loadRecentFunds);
  const [favoriteGroups, setFavoriteGroups] = useState<Array<{ name: string; fund_codes: string[] }>>(loadFavoriteGroups);
  const [enabledReminderCategories, setEnabledReminderCategories] = useState<ReminderCategory[]>(loadReminderPrefs);
  const [manualForm] = Form.useForm();
  const [strategyForm] = Form.useForm();
  const [historyPage, setHistoryPage] = useState(1);
  const [importHistoryPage, setImportHistoryPage] = useState(1);
  const [viewingId, setViewingId] = useState<number | null>(null);
  const [lastRequestMeta, setLastRequestMeta] = useState<AdvisorLastRequestMeta | null>(null);

  // 持仓与个人偏好变化时自动保存到 localStorage
  useEffect(() => {
    saveAdvisorPositions(positions);
  }, [positions]);

  useEffect(() => {
    saveAdvisorViewMode(advisorViewMode);
  }, [advisorViewMode]);

  useEffect(() => {
    saveRecentFunds(recentFunds);
  }, [recentFunds]);

  useEffect(() => {
    saveFavoriteGroups(favoriteGroups);
  }, [favoriteGroups]);

  useEffect(() => {
    saveReminderPrefs(enabledReminderCategories);
  }, [enabledReminderCategories]);

  // 当用户选择基金后，只显示与所选基金匹配的持仓
  const selectedFundCodes = Form.useWatch('fund_codes', manualForm) as string[] | undefined;

  // 策略模式：监听选中的策略 ID
  const selectedStrategyId = Form.useWatch('strategy_id', strategyForm) as number | undefined;

  const queryClient = useQueryClient();
  const analyzeMutation = useAnalyzeFunds();
  const portfolioMutation = usePortfolioAdvice();
  // 主动提醒订阅/推送暂不启用，避免个人自用场景下产生额外偏好请求。
  // const updateReminderPreferenceMutation = useUpdateAdvisorReminderPreference();
  // const createReminderDigestMutation = useCreateAdvisorReminderDigest();
  // const { data: reminderPreferenceData, isLoading: reminderPreferenceLoading } = useAdvisorReminderPreference();
  const { data: persistedPositionsData, isFetched: persistedPositionsFetched } = useAdvisorPositions();
  const { data: importHistoryData, isLoading: importHistoryLoading } = useAdvisorPositionImportHistory({ page: importHistoryPage, page_size: 10 });
  const downloadTemplateMutation = useDownloadAdvisorPositionsTemplate();
  const restoreImportHistoryMutation = useRestoreAdvisorPositionsFromImportHistory();
  const replacePositionsMutation = useReplaceAdvisorPositions();
  const importPositionsMutation = useImportAdvisorPositions();
  const { data: configData } = useAdvisorConfig();
  const { data: featureProfile } = useFeatureProfile();
  const { data: fundOptionsData } = useFundOptions();
  const allFunds = fundOptionsData?.items || [];
  const { data: strategyListData } = useStrategyList({ page_size: 50 });
  const { data: historyData, isLoading: historyLoading } = useAdvisorHistory({ page: historyPage, page_size: 10 });
  const { data: historyDetail } = useAdvisorHistoryDetail(viewingId);
  const showAdvancedAdvisorResearch = featureProfile?.feature_advisor_governance === true;

  useEffect(() => {
    if (!showAdvancedAdvisorResearch && (pageTab === 'validate' || pageTab === 'cross-sectional')) {
      setPageTab('analyze');
    }
  }, [showAdvancedAdvisorResearch, pageTab]);

  const { skipNextPositionSyncRef } = useAdvisorPositionsSync({
    positions,
    persistedPositions: persistedPositionsData,
    persistedPositionsFetched,
    replacePositionsMutation,
    setPositions,
  });

  const strategyOptions = useMemo(
    () => buildAdvisorStrategyOptions(strategyListData?.items),
    [strategyListData?.items],
  );

  // 策略模式：根据选中的策略 ID 提取基金池，用于过滤持仓显示
  const selectedStrategyFundCodes = useMemo(
    () => extractStrategyFundCodes(strategyListData?.items, selectedStrategyId),
    [strategyListData?.items, selectedStrategyId],
  );

  const extraFundCodes = useMemo(() => collectAdvisorExtraFundCodes({
    positions,
    selectedFundCodes,
    selectedStrategyFundCodes,
    historyDetail,
    result,
    lastRequestMeta,
  }), [positions, selectedFundCodes, selectedStrategyFundCodes, historyDetail, result, lastRequestMeta]);

  const fundOptions = useMemo(
    () => buildAdvisorFundOptions(allFunds, extraFundCodes),
    [allFunds, extraFundCodes],
  );

  const fundMap = useMemo(
    () => new Map(allFunds.map((fund) => [fund.code, fund])),
    [allFunds],
  );

  const hotFundCodes = useMemo(() => buildAdvisorHotFundCodes(allFunds), [allFunds]);

  const currentReminderItems = useMemo(() => buildAdviceReminders(result?.advices || []), [result]);
  const historyReminderItems = useMemo(() => historyDetail ? buildHistoryReminders(historyDetail) : [], [historyDetail]);
  const {
    appendManualFundCode,
    rememberFundCodes,
    saveCurrentSelectionAsFavorite,
    applyFavoriteGroup,
  } = useAdvisorFundShortcuts({
    activeTab,
    manualForm,
    selectedStrategyFundCodes,
    favoriteGroups,
    setActiveTab,
    setRecentFunds,
    setFavoriteGroups,
  });

  const handleManualAnalyze = async (values: AdvisorAnalyzeFormValues) => {
    const res = await analyzeMutation.mutateAsync(buildAdvisorAnalyzeRequest(values, positions));
    setResult(res);
    rememberFundCodes(values.fund_codes);
    setLastRequestMeta(buildManualLastRequestMeta(values));
  };

  const handleStrategyAnalyze = async (values: AdvisorStrategyAnalyzeFormValues) => {
    const res = await portfolioMutation.mutateAsync(buildAdvisorPortfolioRequest(values, positions));
    setResult(res);
    // 从 portfolio response 中提取策略信息
    const portfolioRes = res as AdvisorAnalyzeResponse & { strategy_id?: number; strategy_name?: string };
    const strategyOpt = strategyOptions.find(s => s.value === values.strategy_id);
    const rememberedCodes = res.advices.map(a => a.fund_code);
    rememberFundCodes(rememberedCodes);
    setLastRequestMeta(buildStrategyLastRequestMeta({
      values,
      result: portfolioRes,
      strategyLabel: strategyOpt?.label,
    }));
  };

  const {
    activeReminderItems,
    remindersLoading,
    remindersRefreshing,
    handleRefreshReminderInbox,
    handleDismissReminder,
  } = useAdvisorReminderInbox();

  const {
    savingResult,
    hasHighRiskAdvice,
    handleSaveResultWithConfirm,
  } = useAdvisorSaveResult({
    result,
    lastRequestMeta,
    positions,
    manualForm,
    strategyForm,
  });


  /*
   * 主动提醒订阅/摘要推送暂不启用。
   * 如未来恢复多端推送，可重新启用以下保存/预览处理函数，并恢复历史页的 ReminderPreferenceCard。
   */

  const {
    historyRefreshing,
    historyRefreshingId,
    handleDeleteHistory,
    handleRefreshHistory,
    handleLoadHistory,
    handleLoadToForm,
  } = useAdvisorHistoryActions({
    viewingId,
    manualForm,
    strategyForm,
    setViewingId,
    setPositions,
    setActiveTab,
    setPageTab,
  });

  const { addPosition, removePosition, updatePosition } = useAdvisorPositionEditorActions({ setPositions });
  const handleDownloadPositionsTemplate = async (format: 'csv' | 'xlsx') => {
    try {
      const blob = await downloadTemplateMutation.mutateAsync(format);
      downloadBlobFile(`advisor_positions_template.${format}`, blob);
      message.success(`已下载${format.toUpperCase()}模板`);
    } catch {
      // 错误已由拦截器提示
    }
  };

  const handleImportPositions = async (file: File) => {
    try {
      const result = await importPositionsMutation.mutateAsync(file);
      if (result.positions.length > 0) {
        skipNextPositionSyncRef.current = true;
        setPositions(result.positions.map((item) => normalizeAdvisorPositionItem(item)));
        invalidateAdvisorPositionQueries(queryClient);
      }
      setImportHistoryPage(1);
      invalidateAdvisorPositionImportHistoryQueries(queryClient);
      const failedRows = getFailedAdvisorPositionImportRows(result);
      if (shouldShowAdvisorPositionImportReview(result)) {
        Modal.warning({
          title: buildAdvisorPositionImportModalTitle(result),
          width: 760,
          content: <AdvisorPositionImportFailureContent failedRows={failedRows} governanceSummary={result.governance_summary} />,
        });
      } else {
        message.success(buildAdvisorPositionImportSuccessMessage(result));
      }
    } catch {
      // 错误已由 axios 拦截器统一提示
    }
    return false;
  };

  const handleRestorePositionsFromImportHistory = async (importId: number) => {
    try {
      const restored = await restoreImportHistoryMutation.mutateAsync(importId);
      skipNextPositionSyncRef.current = true;
      setPositions(restored.positions.map((item) => normalizeAdvisorPositionItem(item)));
      invalidateAdvisorPositionQueries(queryClient);
      setImportHistoryPage(1);
      invalidateAdvisorPositionImportHistoryQueries(queryClient);
      message.success(buildAdvisorPositionRestoreSuccessMessage(restored));
    } catch {
      // 错误已由 axios 拦截器统一提示
    }
  };

  const {
    handleExportCurrentAuditJson,
    handleExportHistoryAuditJson,
  } = useAdvisorAuditExportHandlers({
    viewMode: advisorViewMode,
    result,
    historyDetail,
    lastRequestMeta,
    positions,
  });

  const isLoading = analyzeMutation.isPending || portfolioMutation.isPending;
  const isError = analyzeMutation.isError || portfolioMutation.isError;
  const error = analyzeMutation.error || portfolioMutation.error;

  const columns = useMemo(() => buildAdvisorAdviceColumns(advisorViewMode), [advisorViewMode]);

  const statsCards = [
    {
      label: '当前持仓数',
      value: positions.length,
      note: '本地持仓编辑器中的基金数量',
      icon: <TeamOutlined />,
      color: '#176bff',
    },
    {
      label: '最近研究记录',
      value: historyData?.total ?? 0,
      note: '历史分析与保存结果总量',
      icon: <BulbOutlined />,
      color: '#1f9d68',
    },
    {
      label: '收藏分组',
      value: favoriteGroups.length,
      note: '常用基金组快捷切换',
      icon: <StarOutlined />,
      color: '#d99614',
    },
    {
      label: '提醒数量',
      value: activeReminderItems.length,
      note: '待处理提醒与研究提示',
      icon: <HeartOutlined />,
      color: '#d84a4a',
    },
  ];

  return (
    <div className="detail-shell">
      <PageHero
        variant="detail"
        eyebrow={<><BulbOutlined /> Advisor Workbench</>}
        title="智能组合检查工作台"
        meta={
          <>
            <span className="detail-pill">{advisorViewMode === 'expert' ? '专家模式' : '新手模式'}</span>
            <span className="detail-pill">{showAdvancedAdvisorResearch ? '高级研究已开启' : '个人默认视图'}</span>
            <span className="detail-pill">持仓 {positions.length} 只</span>
            <span className="detail-pill">最近记录 {historyData?.total || 0} 条</span>
          </>
        }
        description="在这里检查当前组合、导入持仓、保存研究结果、查看历史分析，并在必要时切换到更严格的专家视图。页面结果仅供个人研究辅助，不构成投资建议或交易指令。"
        actions={
          <>
            <Button type={advisorViewMode === 'novice' ? 'primary' : 'default'} onClick={() => setAdvisorViewMode('novice')}>
              新手模式
            </Button>
            <Button type={advisorViewMode === 'expert' ? 'primary' : 'default'} onClick={() => setAdvisorViewMode('expert')}>
              专家模式
            </Button>
            <Link to="/funds">
              <Button icon={<TeamOutlined />}>基金检索</Button>
            </Link>
          </>
        }
        stats={
          <>
            {statsCards.map((item) => (
              <StatCard
                key={item.label}
                label={item.label}
                value={<>{item.icon} {item.value}</>}
                color={item.color}
                note={item.note}
              />
            ))}
          </>
        }
      />

      <AdvisorPageHeader
        viewMode={advisorViewMode}
        showAdvancedResearch={showAdvancedAdvisorResearch}
        onChangeViewMode={setAdvisorViewMode}
      />

      <AdvisorMainTabs
        pageTab={pageTab}
        activeTab={activeTab}
        manualForm={manualForm}
        strategyForm={strategyForm}
        isLoading={isLoading}
        isError={isError}
        error={error}
        recentFunds={recentFunds}
        favoriteGroups={favoriteGroups}
        hotFundCodes={hotFundCodes}
        fundOptions={fundOptions}
        fundMap={fundMap}
        strategyOptions={strategyOptions}
        positions={positions}
        selectedFundCodes={selectedFundCodes}
        selectedStrategyFundCodes={selectedStrategyFundCodes}
        importHistoryData={importHistoryData}
        importHistoryLoading={importHistoryLoading}
        syncingPositions={replacePositionsMutation.isPending}
        downloadingTemplate={downloadTemplateMutation.isPending}
        importingPositions={importPositionsMutation.isPending}
        restoringImportHistory={restoreImportHistoryMutation.isPending}
        restoringImportId={typeof restoreImportHistoryMutation.variables === 'number' ? restoreImportHistoryMutation.variables : null}
        result={result}
        viewMode={advisorViewMode}
        columns={columns}
        currentReminderItems={currentReminderItems}
        enabledReminderCategories={enabledReminderCategories}
        fundTypeCount={configData?.fund_type_awareness?.types?.length}
        savingResult={savingResult}
        hasHighRiskAdvice={hasHighRiskAdvice}
        activeReminderItems={activeReminderItems}
        remindersLoading={remindersLoading}
        remindersRefreshing={remindersRefreshing}
        historyDetail={historyDetail}
        viewingId={viewingId}
        historyReminderItems={historyReminderItems}
        historyItems={historyData?.items || []}
        historyTotal={historyData?.total || 0}
        historyLoading={historyLoading}
        historyPage={historyPage}
        historyRefreshing={historyRefreshing}
        historyRefreshingId={historyRefreshingId}
        showAdvancedResearch={showAdvancedAdvisorResearch}
        onChangePageTab={setPageTab}
        onChangeAnalyzeTab={setActiveTab}
        onManualAnalyze={handleManualAnalyze}
        onStrategyAnalyze={handleStrategyAnalyze}
        onPickFund={appendManualFundCode}
        onApplyFavoriteGroup={applyFavoriteGroup}
        onSaveCurrentSelection={saveCurrentSelectionAsFavorite}
        onDownloadTemplate={handleDownloadPositionsTemplate}
        onImportPositions={handleImportPositions}
        onImportHistoryPageChange={setImportHistoryPage}
        onRestorePositions={handleRestorePositionsFromImportHistory}
        onAddPosition={addPosition}
        onRemovePosition={removePosition}
        onUpdatePosition={updatePosition}
        onChangeReminderCategories={setEnabledReminderCategories}
        onExportCurrentAudit={handleExportCurrentAuditJson}
        onSaveCurrentResult={handleSaveResultWithConfirm}
        onRefreshReminders={handleRefreshReminderInbox}
        onOpenReminder={setViewingId}
        onDismissReminder={handleDismissReminder}
        onBackHistoryDetail={() => setViewingId(null)}
        onRefreshHistory={handleRefreshHistory}
        onExportHistoryAudit={handleExportHistoryAuditJson}
        onLoadHistoryToForm={handleLoadToForm}
        onHistoryPageChange={setHistoryPage}
        onViewHistory={handleLoadHistory}
        onDeleteHistory={handleDeleteHistory}
      />
    </div>
  );
}

