/**
 * Strategy API hooks using TanStack Query.
 *
 * Provides hooks for:
 * - Strategy list
 * - Create strategy
 * - Strategy detail
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type StrategyType = 'dca' | 'momentum' | 'risk_parity' | 'mean_variance' | 'timing' | 'fof';

export interface StrategySummary {
  id: number;
  name: string;
  strategy_type: StrategyType;
  params: Record<string, unknown>;
  universe: string[];
  benchmark: string | null;
  created_at: string;
}

export interface StrategyCreateRequest {
  name: string;
  strategy_type: StrategyType;
  params: Record<string, unknown>;
  universe: string[];
  benchmark?: string;
}

export interface StrategyUpdateRequest {
  name?: string;
  strategy_type?: StrategyType;
  params?: Record<string, unknown>;
  universe?: string[];
  benchmark?: string;
}

export interface StrategyListResponse {
  items: StrategySummary[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

// ---------------------------------------------------------------------------
// Strategy type metadata & param schemas (hardcoded on frontend)
// ---------------------------------------------------------------------------

export interface ParamField {
  key: string;
  label: string;
  type: 'number' | 'string' | 'select';
  default?: unknown;
  min?: number;
  max?: number;
  step?: number;
  options?: { label: string; value: string }[];
  description?: string;
  required?: boolean;
}

export interface StrategyTemplate {
  type: StrategyType;
  label: string;
  description: string;
  params: ParamField[];
}

export const STRATEGY_TEMPLATES: StrategyTemplate[] = [
  {
    type: 'dca',
    label: '定投策略',
    description: '定额定投、价值平均、智能定投（均线偏离加倍）',
    params: [
      { key: 'amount', label: '每期投入金额', type: 'number', default: 1000, min: 100, step: 100, required: true },
      { key: 'frequency', label: '定投频率', type: 'select', default: 'monthly', options: [
        { label: '每周', value: 'weekly' },
        { label: '每两周', value: 'biweekly' },
        { label: '每月', value: 'monthly' },
      ], required: true },
      { key: 'dca_type', label: '定投模式', type: 'select', default: 'fixed', options: [
        { label: '定额', value: 'fixed' },
        { label: '价值平均', value: 'value_averaging' },
        { label: '智能定投', value: 'smart' },
      ] },
      { key: 'ma_window', label: '均线窗口（智能定投）', type: 'number', default: 250, min: 20, max: 500, description: '仅智能定投模式使用' },
    ],
  },
  {
    type: 'momentum',
    label: '动量轮动策略',
    description: '基于动量/Sharpe/IR 因子的 Top-N 轮动',
    params: [
      { key: 'lookback_months', label: '回看月数', type: 'number', default: 6, min: 1, max: 24, required: true },
      { key: 'top_n', label: '持有数量', type: 'number', default: 3, min: 1, max: 20, required: true },
      { key: 'rebalance_freq', label: '调仓频率', type: 'select', default: 'monthly', options: [
        { label: '每周', value: 'weekly' },
        { label: '每月', value: 'monthly' },
        { label: '每季', value: 'quarterly' },
      ], required: true },
      { key: 'score_factor', label: '评分因子', type: 'select', default: 'return', options: [
        { label: '收益率', value: 'return' },
        { label: 'Sharpe', value: 'sharpe' },
        { label: '信息比率', value: 'information_ratio' },
      ] },
    ],
  },
  {
    type: 'risk_parity',
    label: '风险平价策略',
    description: '等风险贡献权重优化',
    params: [
      { key: 'rebalance_freq', label: '调仓频率', type: 'select', default: 'monthly', options: [
        { label: '每月', value: 'monthly' },
        { label: '每季', value: 'quarterly' },
      ], required: true },
      { key: 'cov_method', label: '协方差估计方法', type: 'select', default: 'sample', options: [
        { label: '样本协方差', value: 'sample' },
        { label: '指数加权', value: 'ewm' },
        { label: '收缩估计', value: 'shrinkage' },
      ] },
      { key: 'lookback_days', label: '回看天数', type: 'number', default: 252, min: 60, max: 756 },
    ],
  },
  {
    type: 'mean_variance',
    label: '均值-方差优化',
    description: '均值-方差优化与 Black-Litterman 模型',
    params: [
      { key: 'rebalance_freq', label: '调仓频率', type: 'select', default: 'monthly', options: [
        { label: '每月', value: 'monthly' },
        { label: '每季', value: 'quarterly' },
      ], required: true },
      { key: 'risk_aversion', label: '风险厌恶系数', type: 'number', default: 2.5, min: 0.5, max: 10, step: 0.5 },
      { key: 'use_bl', label: '使用 Black-Litterman', type: 'select', default: 'no', options: [
        { label: '否', value: 'no' },
        { label: '是', value: 'yes' },
      ] },
      { key: 'lookback_days', label: '回看天数', type: 'number', default: 252, min: 60, max: 756 },
    ],
  },
  {
    type: 'timing',
    label: '择时策略',
    description: '双均线、MACD、估值分位数择时',
    params: [
      { key: 'method', label: '择时方法', type: 'select', default: 'dual_ma', options: [
        { label: '双均线', value: 'dual_ma' },
        { label: 'MACD', value: 'macd' },
        { label: '估值分位数', value: 'valuation' },
      ], required: true },
      { key: 'fast_window', label: '快线窗口', type: 'number', default: 20, min: 5, max: 60 },
      { key: 'slow_window', label: '慢线窗口', type: 'number', default: 60, min: 20, max: 250 },
    ],
  },
  {
    type: 'fof',
    label: 'FOF 策略',
    description: '多因子打分筛选 + 组合优化',
    params: [
      { key: 'top_n', label: '持有基金数', type: 'number', default: 5, min: 2, max: 30, required: true },
      { key: 'rebalance_freq', label: '调仓频率', type: 'select', default: 'quarterly', options: [
        { label: '每月', value: 'monthly' },
        { label: '每季', value: 'quarterly' },
      ], required: true },
      { key: 'optimization', label: '优化方法', type: 'select', default: 'equal_weight', options: [
        { label: '等权', value: 'equal_weight' },
        { label: '风险平价', value: 'risk_parity' },
        { label: '均值-方差', value: 'mean_variance' },
      ] },
      { key: 'factor_weights', label: '因子权重(JSON)', type: 'string', default: '{"sharpe":0.4,"max_drawdown":0.3,"return":0.3}', description: 'JSON 格式的因子权重' },
    ],
  },
];

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

async function fetchStrategies(params?: { page?: number; page_size?: number; strategy_type?: string }): Promise<StrategyListResponse> {
  const { data } = await apiClient.get<StrategyListResponse>('/v1/strategies', { params });
  return data;
}

async function fetchStrategy(id: number): Promise<StrategySummary> {
  const { data } = await apiClient.get<StrategySummary>(`/v1/strategies/${id}`);
  return data;
}

export interface StrategyDateRange {
  earliest_date: string | null;
  fund_dates: Record<string, string | null>;
}

async function fetchStrategyDateRange(id: number): Promise<StrategyDateRange> {
  const { data } = await apiClient.get<StrategyDateRange>(`/v1/strategies/${id}/date-range`);
  return data;
}

async function createStrategy(payload: StrategyCreateRequest): Promise<StrategySummary> {
  const { data } = await apiClient.post<StrategySummary>('/v1/strategies', payload);
  return data;
}

async function updateStrategy({ id, ...payload }: StrategyUpdateRequest & { id: number }): Promise<StrategySummary> {
  const { data } = await apiClient.put<StrategySummary>(`/v1/strategies/${id}`, payload);
  return data;
}

async function deleteStrategy(id: number): Promise<void> {
  await apiClient.delete(`/v1/strategies/${id}`);
}

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useStrategyList(params?: { page?: number; page_size?: number; strategy_type?: string }) {
  return useQuery({
    queryKey: ['strategies', params],
    queryFn: () => fetchStrategies(params),
  });
}

export function useStrategy(id: number | null) {
  return useQuery({
    queryKey: ['strategies', id],
    queryFn: () => fetchStrategy(id!),
    enabled: id !== null,
  });
}

export function useCreateStrategy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createStrategy,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['strategies'] });
    },
  });
}

export function useUpdateStrategy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updateStrategy,
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ['strategies'] });
      queryClient.invalidateQueries({ queryKey: ['strategies', variables.id] });
    },
  });
}

export function useDeleteStrategy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: deleteStrategy,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['strategies'] });
    },
  });
}

export function useStrategyDateRange(id: number | null) {
  return useQuery({
    queryKey: ['strategies', id, 'date-range'],
    queryFn: () => fetchStrategyDateRange(id!),
    enabled: id !== null,
  });
}
