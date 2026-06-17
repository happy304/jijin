import { useMutation, useQuery } from '@tanstack/react-query';
import apiClient from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface StrategyGenRequest {
  description: string;
}

export interface StrategyGenResponse {
  strategy_type: string;
  name: string;
  params: Record<string, unknown>;
  universe: Record<string, unknown>;
  reasoning: string;
  is_valid: boolean;
  validation_errors: string[];
}

export interface ProviderUsage {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
}

export interface UseCaseUsage {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
}

export interface BudgetStatus {
  daily_spend_usd: number;
  daily_limit_usd: number;
  daily_remaining_usd: number;
  monthly_spend_usd: number;
  monthly_limit_usd: number;
  monthly_remaining_usd: number;
  date: string;
  month: string;
}

export interface AIUsageResponse {
  total_calls: number;
  successful_calls: number;
  failed_calls: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
  avg_latency_ms: number | null;
  by_provider: Record<string, ProviderUsage>;
  by_use_case: Record<string, UseCaseUsage>;
  budget: BudgetStatus | null;
  period_start: string;
  period_end: string;
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

async function generateStrategy(req: StrategyGenRequest): Promise<StrategyGenResponse> {
  const { data } = await apiClient.post<StrategyGenResponse>('/v1/ai/strategy-gen', req);
  return data;
}

async function fetchAIUsage(days: number = 30): Promise<AIUsageResponse> {
  const { data } = await apiClient.get<AIUsageResponse>('/v1/ai/usage', {
    params: { days },
  });
  return data;
}

// ---------------------------------------------------------------------------
// React Query hooks
// ---------------------------------------------------------------------------

export function useGenerateStrategy() {
  return useMutation({
    mutationFn: generateStrategy,
  });
}

export function useAIUsage(days: number = 30) {
  return useQuery({
    queryKey: ['ai-usage', days],
    queryFn: () => fetchAIUsage(days),
    refetchInterval: 60_000, // Refresh every minute
  });
}
