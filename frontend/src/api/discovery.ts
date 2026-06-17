/**
 * Discovery API hooks using TanStack Query.
 *
 * Provides hooks for:
 * - Fund ranking list with filters
 * - Discovery statistics
 * - Manual trigger
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { message } from 'antd';
import apiClient from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface RankingItem {
  fund_code: string;
  fund_name: string | null;
  snapshot_date: string;
  sort_metric: string;
  rank_position: number;
  fund_type: string | null;
  daily_return: string | null;
  weekly_return: string | null;
  monthly_return: string | null;
  quarterly_return: string | null;
  half_year_return: string | null;
  yearly_return: string | null;
}

export interface PaginatedRankings {
  items: RankingItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface DiscoveryStats {
  total_funds_tracked: number;
  funds_from_discovery: number;
  latest_snapshot_date: string | null;
  ranking_records_count: number;
  unique_funds_in_rankings: number;
  dimensions_tracked: string[];
}

export interface TriggerResponse {
  status: string;
  task_id: string;
  message: string;
}

export interface RankingListParams {
  page?: number;
  page_size?: number;
  snapshot_date?: string;
  sort_metric?: string;
  fund_type?: string;
  fund_code?: string;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

async function fetchRankings(params: RankingListParams): Promise<PaginatedRankings> {
  const { data } = await apiClient.get<PaginatedRankings>('/v1/discovery/rankings', { params });
  return data;
}

async function fetchDiscoveryStats(): Promise<DiscoveryStats> {
  const { data } = await apiClient.get<DiscoveryStats>('/v1/discovery/stats');
  return data;
}

async function triggerDiscovery(): Promise<TriggerResponse> {
  const { data } = await apiClient.post<TriggerResponse>('/v1/discovery/trigger');
  return data;
}

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useRankingList(params: RankingListParams) {
  return useQuery({
    queryKey: ['discovery', 'rankings', params],
    queryFn: () => fetchRankings(params),
    placeholderData: (previousData) => previousData,
  });
}

export function useDiscoveryStats() {
  return useQuery({
    queryKey: ['discovery', 'stats'],
    queryFn: fetchDiscoveryStats,
    refetchInterval: 60000, // 每分钟刷新
  });
}

export function useTriggerDiscovery() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: triggerDiscovery,
    onSuccess: (data) => {
      message.success(data.message);
      // 触发后刷新统计数据
      queryClient.invalidateQueries({ queryKey: ['discovery'] });
    },
    onError: () => {
      message.error('触发发现任务失败');
    },
  });
}

// ---------------------------------------------------------------------------
// 4433 法则筛选
// ---------------------------------------------------------------------------

export interface Filter4433Request {
  fund_type?: string;
  year1_percentile?: number;
  year2_percentile?: number;
  month6_percentile?: number;
  month3_percentile?: number;
  min_inception_years?: number;
}

export interface Fund4433Item {
  fund_code: string;
  fund_name: string | null;
  fund_type: string | null;
  rank_1y: number | null;
  rank_6m: number | null;
  rank_3m: number | null;
  return_1y: number | null;
  return_6m: number | null;
  return_3m: number | null;
  pass_all: boolean;
}

export interface Filter4433Response {
  total_screened: number;
  passed_count: number;
  pass_rate: number;
  funds: Fund4433Item[];
}

async function filter4433(params?: Filter4433Request): Promise<Filter4433Response> {
  const { data } = await apiClient.post<Filter4433Response>('/v1/discovery/4433', params || {});
  return data;
}

export function useFilter4433() {
  return useMutation({
    mutationFn: (params?: Filter4433Request) => filter4433(params),
  });
}
