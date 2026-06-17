import { useCallback } from 'react';
import { message, Modal, type FormInstance } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import { useSaveAdvisorResult, type AdvisorAnalyzeResponse } from '@/api/advisor';
import {
  buildAdvisorSaveResultPayload,
  buildAdvisorUserProfileFallback,
  hasHighRiskAdvisorAdvice,
} from '@/utils/advisorSavePayload';
import { invalidateAdvisorHistoryQueries } from '@/utils/advisorQueryInvalidation';
import type { AdvisorLastRequestMeta } from '@/utils/advisorRequestPayloads';
import type { AdvisorPositionItem } from '@/utils/advisorPositions';

export function useAdvisorSaveResult({
  result,
  lastRequestMeta,
  positions,
  manualForm,
  strategyForm,
}: {
  result: AdvisorAnalyzeResponse | null;
  lastRequestMeta?: AdvisorLastRequestMeta | null;
  positions: AdvisorPositionItem[];
  manualForm: FormInstance;
  strategyForm: FormInstance;
}) {
  const queryClient = useQueryClient();
  const saveMutation = useSaveAdvisorResult();
  const hasHighRiskAdvice = hasHighRiskAdvisorAdvice(result);

  const handleSaveResult = useCallback(async () => {
    if (!result) return;
    try {
      const saveResult = await saveMutation.mutateAsync(buildAdvisorSaveResultPayload({
        result,
        lastRequestMeta,
        positions,
        userProfileFallback: buildAdvisorUserProfileFallback(
          manualForm.getFieldsValue(),
          strategyForm.getFieldsValue(),
        ),
      }));
      message.success(saveResult.message);
      invalidateAdvisorHistoryQueries(queryClient);
    } catch {
      message.error('保存失败');
    }
  }, [result, saveMutation, lastRequestMeta, positions, manualForm, strategyForm, queryClient]);

  const handleSaveResultWithConfirm = useCallback(() => {
    if (!result) return;
    if (!hasHighRiskAdvice) {
      void handleSaveResult();
      return;
    }
    Modal.confirm({
      title: '存在高风险检查项，确认保存？',
      content: '当前组合检查中包含强动作、适当性不匹配、数据质量告警或高过拟合风险。建议先复核数据与风险来源后再保存。',
      okText: '仍然保存',
      cancelText: '返回检查',
      onOk: () => handleSaveResult(),
    });
  }, [handleSaveResult, hasHighRiskAdvice, result]);

  return {
    savingResult: saveMutation.isPending,
    hasHighRiskAdvice,
    handleSaveResultWithConfirm,
  };
}
