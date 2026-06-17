/**
 * Backtest Sharpe Inference API — PSR / DSR / 95% CI.
 *
 * Endpoint: GET /api/v1/backtests/{runId}/inference?n_trials=N
 */

import { useQuery } from '@tanstack/react-query';
import apiClient from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SharpeInference {
  run_id: number;
  sharpe_observed: number | null;
  sharpe_annualized: number | null;
  n_observations: number;
  skewness: number | null;
  excess_kurtosis: number | null;
  /** Probabilistic Sharpe Ratio: P(true Sharpe > 0) */
  psr: number | null;
  /** Deflated Sharpe Ratio: PSR adjusted for n_trials multiple-testing */
  dsr: number | null;
  n_trials: number;
  psr_significant: boolean;
  dsr_significant: boolean;
  /** 95% lower bound of annualized Sharpe */
  ci_lower: number | null;
  /** 95% upper bound of annualized Sharpe */
  ci_upper: number | null;
  note: string | null;
}

// ---------------------------------------------------------------------------
// API function
// ---------------------------------------------------------------------------

async function fetchSharpeInference(
  runId: number,
  nTrials: number = 1,
): Promise<SharpeInference> {
  const { data } = await apiClient.get<SharpeInference>(
    `/v1/backtests/${runId}/inference`,
    { params: { n_trials: nTrials } },
  );
  return data;
}

// ---------------------------------------------------------------------------
// Query hook
// ---------------------------------------------------------------------------

export function useSharpeInference(runId: number, nTrials: number = 1) {
  return useQuery({
    queryKey: ['backtestInference', runId, nTrials],
    queryFn: () => fetchSharpeInference(runId, nTrials),
    enabled: runId > 0,
    staleTime: 60_000, // 1 minute
  });
}
