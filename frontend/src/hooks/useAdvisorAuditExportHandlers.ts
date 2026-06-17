import { useCallback } from 'react';
import { message } from 'antd';
import type { AdvisorAnalyzeResponse, AdvisorHistoryDetailResponse } from '@/api/advisor';
import { buildAdvisorAuditExportPayload } from '@/utils/advisorAuditExport';
import type { AdvisorLastRequestMeta } from '@/utils/advisorRequestPayloads';
import { buildAdvisorPositionsDetail, type AdvisorPositionItem } from '@/utils/advisorPositions';
import type { AdvisorViewMode } from '@/utils/advisorPreferences';
import { downloadJsonFile } from '@/utils/fileDownload';

export function useAdvisorAuditExportHandlers({
  viewMode,
  result,
  historyDetail,
  lastRequestMeta,
  positions,
}: {
  viewMode: AdvisorViewMode;
  result: AdvisorAnalyzeResponse | null;
  historyDetail?: AdvisorHistoryDetailResponse | null;
  lastRequestMeta?: AdvisorLastRequestMeta | null;
  positions: AdvisorPositionItem[];
}) {
  const handleExportCurrentAuditJson = useCallback(() => {
    if (!result) return;
    downloadJsonFile(
      `advisor-audit-${result.advice_date}-${viewMode}.json`,
      buildAdvisorAuditExportPayload({
        scope: 'current_result',
        viewMode,
        adviceDate: result.advice_date,
        riskLevel: result.risk_level,
        totalCapital: result.total_capital,
        fundCodes: lastRequestMeta?.fund_codes || result.advices.map((advice) => advice.fund_code),
        strategyName: lastRequestMeta?.strategy_name || null,
        summary: result.summary,
        userProfile: result.user_profile || null,
        positionsDetail: buildAdvisorPositionsDetail(positions),
        executionPlanStatus: null,
        advices: result.advices,
      }),
    );
    message.success('已导出当前建议审计 JSON');
  }, [lastRequestMeta, positions, result, viewMode]);

  const handleExportHistoryAuditJson = useCallback(() => {
    if (!historyDetail) return;
    downloadJsonFile(
      `advisor-history-audit-${historyDetail.id}-${historyDetail.advice_date}-${viewMode}.json`,
      buildAdvisorAuditExportPayload({
        scope: 'history_detail',
        viewMode,
        adviceDate: historyDetail.advice_date,
        riskLevel: historyDetail.risk_level,
        totalCapital: historyDetail.total_capital,
        fundCodes: historyDetail.fund_codes,
        strategyName: historyDetail.strategy_name,
        summary: historyDetail.summary,
        userProfile: historyDetail.user_profile,
        positionsDetail: historyDetail.positions_detail,
        executionContext: historyDetail.execution_context,
        executionPlanStatus: historyDetail.execution_plan_status,
        advices: historyDetail.advices,
        sourceId: historyDetail.id,
      }),
    );
    message.success('已导出历史检查审计 JSON');
  }, [historyDetail, viewMode]);

  return {
    handleExportCurrentAuditJson,
    handleExportHistoryAuditJson,
  };
}
