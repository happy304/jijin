import { useCallback } from 'react';
import { message } from 'antd';
import type { FormInstance } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import {
  useDeleteAdvisorHistory,
  useRefreshAdvisorHistory,
  type AdvisorHistoryItem,
} from '@/api/advisor';
import { useAdvisorHistoryFormLoader } from '@/hooks/useAdvisorHistoryFormLoader';
import {
  invalidateAdvisorHistoryQueries,
  invalidateAdvisorPerformanceQueries,
} from '@/utils/advisorQueryInvalidation';
import type { AdvisorPositionItem } from '@/utils/advisorPositions';

export function useAdvisorHistoryActions({
  viewingId,
  manualForm,
  strategyForm,
  setViewingId,
  setPositions,
  setActiveTab,
  setPageTab,
}: {
  viewingId: number | null;
  manualForm: FormInstance;
  strategyForm: FormInstance;
  setViewingId: (id: number | null) => void;
  setPositions: (positions: AdvisorPositionItem[]) => void;
  setActiveTab: (tab: string) => void;
  setPageTab: (tab: string) => void;
}) {
  const queryClient = useQueryClient();
  const deleteMutation = useDeleteAdvisorHistory();
  const refreshMutation = useRefreshAdvisorHistory();

  const handleDeleteHistory = useCallback(async (id: number) => {
    try {
      await deleteMutation.mutateAsync(id);
      message.success('已删除');
      invalidateAdvisorHistoryQueries(queryClient);
      if (viewingId === id) setViewingId(null);
    } catch {
      message.error('删除失败');
    }
  }, [deleteMutation, queryClient, setViewingId, viewingId]);

  const handleRefreshHistory = useCallback(async (id: number) => {
    try {
      const res = await refreshMutation.mutateAsync(id);
      message.success(res.message);
      if (res.id) setViewingId(res.id);
      invalidateAdvisorHistoryQueries(queryClient);
      invalidateAdvisorPerformanceQueries(queryClient);
    } catch {
      message.error('更新失败');
    }
  }, [refreshMutation, queryClient, setViewingId]);

  const handleLoadHistory = useCallback((item: AdvisorHistoryItem) => {
    setViewingId(item.id);
  }, [setViewingId]);

  const handleLoadToForm = useAdvisorHistoryFormLoader({
    manualForm,
    strategyForm,
    setPositions,
    setActiveTab,
    setPageTab,
    setViewingId,
  });

  return {
    historyRefreshing: refreshMutation.isPending,
    historyRefreshingId: typeof refreshMutation.variables === 'number' ? refreshMutation.variables : null,
    handleDeleteHistory,
    handleRefreshHistory,
    handleLoadHistory,
    handleLoadToForm,
  };
}
