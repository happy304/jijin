/**
 * Simulation (Monte Carlo) API hooks using TanStack Query.
 *
 * Provides hooks for:
 * - Simulation list
 * - Submit simulation
 * - Simulation detail (status + metrics)
 * - Percentile paths (fan chart data)
 * - Delete simulation
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type SimulationMethod = 'gbm' | 'bootstrap' | 'hybrid';

export interface SimulationSubmitRequest {
  strategy_id: number;
  horizon_days?: number;
  num_simulations?: number;
  method?: SimulationMethod;
  initial_capital?: number;
  confidence_levels?: number[];
  target_return?: number | null;
  lookback_days?: number;
}

export interface SimulationSubmitResponse {
  run_id: number;
  status: string;
  message: string;
}

export interface SimulationNavDataStaleWarning {
  message?: string;
  [key: string]: unknown;
}

export interface SimulationNavQualityWarning {
  message?: string;
  funds?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface SimulationStatus {
  id: number;
  strategy_id: number | null;
  strategy_name: string | null;
  horizon_days: number;
  num_simulations: number;
  method: string;
  initial_capital: string | null;
  target_return: number | null;
  status: string;
  progress: number;
  progress_message?: string | null;
  metrics: SimulationMetrics | null;
  nav_data_stale?: SimulationNavDataStaleWarning | null;
  nav_quality_warning?: SimulationNavQualityWarning | null;
  error_msg: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string | null;
}

export interface SimulationMetrics {
  expected_return: number;
  median_return: number;
  volatility: number;
  var: Record<string, number>;
  cvar: Record<string, number>;
  max_drawdown_median: number;
  max_drawdown_p95: number;
  target_return: number | null;
  target_probability: number | null;
  terminal_wealth_mean: number;
  terminal_wealth_median: number;
  terminal_wealth_p5: number;
  terminal_wealth_p95: number;
  extended?: ExtendedRiskMetrics;
  funds_used?: string[];
  data_points?: number;
}

export interface ExtendedRiskMetrics {
  predicted_sharpe: number;
  predicted_sortino: number;
  predicted_calmar: number;
  prob_positive_return: number;
  prob_loss_gt_10pct: number;
  prob_loss_gt_20pct: number;
  prob_ruin: number;
  skewness: number;
  kurtosis: number;
}

export interface PercentilePathsResponse {
  run_id: number;
  horizon_days: number;
  initial_capital: number;
  paths: Record<string, number[]>;
}

// ---------------------------------------------------------------------------
// Simulation method metadata
// ---------------------------------------------------------------------------

export const SIMULATION_METHODS: { value: SimulationMethod; label: string; description: string }[] = [
  {
    value: 'gbm',
    label: '几何布朗运动 (GBM)',
    description: '假设对数收益率服从正态分布，经典金融模型',
  },
  {
    value: 'bootstrap',
    label: '自助法 (Bootstrap)',
    description: '从历史收益率中有放回抽样，保留真实分布特征（肥尾）',
  },
  {
    value: 'hybrid',
    label: '混合方法 (Hybrid)',
    description: 'GBM 为基础，用历史极端值修正尾部，兼顾理论与实证',
  },
];

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

async function fetchSimulations(params?: {
  strategy_id?: number;
  limit?: number;
}): Promise<SimulationStatus[]> {
  const { data } = await apiClient.get<SimulationStatus[]>('/v1/simulations', { params });
  return data;
}

async function fetchSimulation(id: number): Promise<SimulationStatus> {
  const { data } = await apiClient.get<SimulationStatus>(`/v1/simulations/${id}`);
  return data;
}

async function fetchSimulationPaths(id: number): Promise<PercentilePathsResponse> {
  const { data } = await apiClient.get<PercentilePathsResponse>(`/v1/simulations/${id}/paths`);
  return data;
}

async function submitSimulation(payload: SimulationSubmitRequest): Promise<SimulationSubmitResponse> {
  const { data } = await apiClient.post<SimulationSubmitResponse>('/v1/simulations', payload);
  return data;
}

async function rerunSimulation(id: number): Promise<SimulationSubmitResponse> {
  const { data } = await apiClient.post<SimulationSubmitResponse>(`/v1/simulations/${id}/rerun`);
  return data;
}

async function deleteSimulation(id: number): Promise<void> {
  await apiClient.delete(`/v1/simulations/${id}`);
}

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useSimulationList(params?: { strategy_id?: number; limit?: number }) {
  return useQuery({
    queryKey: ['simulations', params],
    queryFn: () => fetchSimulations(params),
    refetchInterval: (query) => {
      const items = query.state.data;
      if (items?.some((item) => item.status === 'pending' || item.status === 'running')) {
        return 2000;
      }
      return false;
    },
  });
}

export function useSimulation(id: number | null) {
  return useQuery({
    queryKey: ['simulation', id],
    queryFn: () => fetchSimulation(id!),
    enabled: id !== null,
    refetchInterval: (query) => {
      // Auto-refresh while running
      const status = query.state.data?.status;
      if (status === 'pending' || status === 'running') {
        return 2000;
      }
      return false;
    },
  });
}

export function useSimulationPaths(id: number | null, enabled = true) {
  return useQuery({
    queryKey: ['simulation-paths', id],
    queryFn: () => fetchSimulationPaths(id!),
    enabled: enabled && id !== null,
  });
}

// ---------------------------------------------------------------------------
// Mutation hooks
// ---------------------------------------------------------------------------

export function useSubmitSimulation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: submitSimulation,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['simulations'] });
    },
  });
}

export function useRerunSimulation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: rerunSimulation,
    onSuccess: (_data, runId) => {
      queryClient.invalidateQueries({ queryKey: ['simulations'] });
      queryClient.invalidateQueries({ queryKey: ['simulation', runId] });
      queryClient.invalidateQueries({ queryKey: ['simulation-paths', runId] });
    },
  });
}

export function useDeleteSimulation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteSimulation,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['simulations'] });
    },
  });
}
