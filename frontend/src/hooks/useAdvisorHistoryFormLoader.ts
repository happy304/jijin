import { useCallback } from 'react';
import type { FormInstance } from 'antd';
import { message } from 'antd';
import type { AdvisorHistoryDetailResponse } from '@/api/advisor';
import {
  buildManualFormValuesFromHistoryDetail,
  buildPositionsFromHistoryDetail,
  buildStrategyFormValuesFromHistoryDetail,
} from '@/utils/advisorHistoryRestore';
import type { AdvisorPositionItem } from '@/utils/advisorPositions';

export function useAdvisorHistoryFormLoader({
  manualForm,
  strategyForm,
  setPositions,
  setActiveTab,
  setPageTab,
  setViewingId,
}: {
  manualForm: FormInstance;
  strategyForm: FormInstance;
  setPositions: (positions: AdvisorPositionItem[]) => void;
  setActiveTab: (tab: string) => void;
  setPageTab: (tab: string) => void;
  setViewingId: (id: number | null) => void;
}) {
  return useCallback((detail: AdvisorHistoryDetailResponse) => {
    // 恢复持仓（始终用历史记录中的持仓覆盖当前值；历史记录中没有持仓时保留当前已有持仓）
    const restoredPositions = buildPositionsFromHistoryDetail(detail);
    if (restoredPositions) {
      setPositions(restoredPositions);
    }

    // 恢复表单参数
    if (detail.strategy_id) {
      setActiveTab('strategy');
      strategyForm.setFieldsValue(buildStrategyFormValuesFromHistoryDetail(detail));
    } else {
      setActiveTab('manual');
      manualForm.setFieldsValue(buildManualFormValuesFromHistoryDetail(detail));
    }

    // 切换到生成组合检查 Tab
    setPageTab('analyze');
    setViewingId(null);
    message.success('已加载历史参数到表单，可修改后重新生成');
  }, [manualForm, setActiveTab, setPageTab, setPositions, setViewingId, strategyForm]);
}
