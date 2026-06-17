import type { QueryClient } from '@tanstack/react-query';

export function invalidateAdvisorHistoryQueries(queryClient: QueryClient): void {
  queryClient.invalidateQueries({ queryKey: ['advisor-history'] });
  queryClient.invalidateQueries({ queryKey: ['advisor-history-detail'] });
}

export function invalidateAdvisorReminderQueries(queryClient: QueryClient): void {
  queryClient.invalidateQueries({ queryKey: ['advisor-reminders'] });
  queryClient.invalidateQueries({ queryKey: ['advisor-history-detail'] });
}

export function invalidateAdvisorPerformanceQueries(queryClient: QueryClient): void {
  queryClient.invalidateQueries({ queryKey: ['advisor-performance'] });
}

export function invalidateAdvisorPositionQueries(queryClient: QueryClient): void {
  queryClient.invalidateQueries({ queryKey: ['advisor-positions'] });
}

export function invalidateAdvisorPositionImportHistoryQueries(queryClient: QueryClient): void {
  queryClient.invalidateQueries({ queryKey: ['advisor-position-import-history'] });
}
