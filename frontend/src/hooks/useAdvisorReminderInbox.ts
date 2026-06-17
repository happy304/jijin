import { useCallback, useMemo } from 'react';
import { message } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import {
  useAdvisorReminders,
  useRefreshAdvisorReminders,
  useUpdateAdvisorReminder,
} from '@/api/advisor';
import { buildActiveReminderListItems } from '@/utils/advisorReminderBuilders';
import { invalidateAdvisorReminderQueries } from '@/utils/advisorQueryInvalidation';

export function useAdvisorReminderInbox() {
  const queryClient = useQueryClient();
  const refreshRemindersMutation = useRefreshAdvisorReminders();
  const updateReminderMutation = useUpdateAdvisorReminder();
  const { data: reminderInboxData, isLoading: remindersLoading } = useAdvisorReminders({ status: 'active', page: 1, page_size: 20 });

  const activeReminderItems = useMemo(
    () => buildActiveReminderListItems(reminderInboxData?.items || []),
    [reminderInboxData],
  );

  const handleRefreshReminderInbox = useCallback(async () => {
    try {
      const res = await refreshRemindersMutation.mutateAsync({ lookback_days: 120, limit: 200 });
      message.success(`已刷新 ${res.processed} 条建议的提醒`);
      invalidateAdvisorReminderQueries(queryClient);
    } catch {
      message.error('刷新提醒失败');
    }
  }, [refreshRemindersMutation, queryClient]);

  const handleDismissReminder = useCallback(async (reminderId: number) => {
    try {
      await updateReminderMutation.mutateAsync({ reminderId, status: 'dismissed' });
      message.success('已忽略提醒');
      invalidateAdvisorReminderQueries(queryClient);
    } catch {
      message.error('更新提醒失败');
    }
  }, [queryClient, updateReminderMutation]);

  return {
    activeReminderItems,
    remindersLoading,
    remindersRefreshing: refreshRemindersMutation.isPending,
    handleRefreshReminderInbox,
    handleDismissReminder,
  };
}
