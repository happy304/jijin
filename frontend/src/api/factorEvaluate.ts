/**
 * Factor IC / Quintile Evaluation API.
 *
 * Endpoint: POST /api/v1/factors/evaluate
 */

import { useMutation } from '@tanstack/react-query';
import apiClient from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ICStats {
  ic_mean: number | null;
  ic_std: number | null;
  ic_ir: number | null;
  ic_ir_annualized: number | null;
  ic_t_stat: number | null;
  ic_p_value: number | null;
  ic_positive_rate: number | null;
  ic_significant_rate: number | null;
  n_periods: number;
  method: string;
}

export interface QuintileResult {
  n_groups: number;
  annualized_returns: Record<string, number | null>;
  sharpes: Record<string, number | null>;
  long_short_sharpe: number | null;
  long_short_total_return: number | null;
  /** 1 = monotonically increasing, -1 = decreasing, 0 = non-monotonic */
  monotonicity: number;
}

export interface FactorEvaluateRequest {
  fund_codes: string[];
  factor_name: string;
  start_date?: string;
  end_date?: string;
  /** Pandas freq alias: 'D' | 'W' | 'M' | 'Q' */
  rebalance_freq?: string;
  decay_horizons?: number[];
  n_groups?: number;
  method?: 'pearson' | 'spearman';
}

export interface FactorEvaluateResponse {
  factor_name: string;
  n_assets: number;
  n_dates: number;
  ic_pearson: ICStats | null;
  ic_spearman: ICStats | null;
  ic_decay: Record<string, ICStats>;
  quintile: QuintileResult | null;
  note: string | null;
}

// ---------------------------------------------------------------------------
// API function
// ---------------------------------------------------------------------------

async function evaluateFactor(
  request: FactorEvaluateRequest,
): Promise<FactorEvaluateResponse> {
  const { data } = await apiClient.post<FactorEvaluateResponse>(
    '/v1/factors/evaluate',
    request,
  );
  return data;
}

// ---------------------------------------------------------------------------
// Mutation hook (POST, not idempotent)
// ---------------------------------------------------------------------------

export function useEvaluateFactor() {
  return useMutation({
    mutationFn: evaluateFactor,
  });
}
