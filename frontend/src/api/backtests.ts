/**
 * Backtest API hooks using TanStack Query.
 *
 * Provides hooks for:
 * - Submit backtest
 * - Get backtest status/result
 * - Get equity curve
 * - Get trade history (paginated)
 * - Get attribution results
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import apiClient from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface BacktestSubmitRequest {
  strategy_id: number;
  start_date: string;
  end_date: string;
  initial_capital: number;
}

export interface BacktestSubmitResponse {
  run_id: number;
  status: string;
  message?: string;
}

export type BacktestStatus = 'pending' | 'running' | 'done' | 'failed';

export interface BacktestQuality {
  lookahead_guard?: boolean;
  cash_arrival_delay_modelled?: boolean;
  lot_level_fee_modelled?: boolean;
  pit_data_quality?: 'strict' | 'fallback' | 'missing' | string;
  nav_publication_lag_modelled?: boolean;
  survivorship_bias_control?: 'full' | 'partial' | 'none' | string;
  vectorized_simplification?: boolean;
  decision_grade?: 'decision_support' | 'research_approximation' | string;
  warnings?: string[];
}

export interface BacktestMetrics {
  quality?: BacktestQuality;
  total_return?: number;
  annualized_return?: number;
  sharpe?: number;
  max_drawdown?: number;
  volatility?: number;
  sortino?: number;
  calmar?: number;
  win_rate?: number;
  profit_factor?: number;
  cashflow_win_rate_estimate?: number;
  cashflow_profit_factor_estimate?: number;
  trade_win_rate?: number; // deprecated: use cashflow_win_rate_estimate
  trade_profit_factor?: number; // deprecated: use cashflow_profit_factor_estimate
  trade_metrics_deprecated?: boolean;
  trade_metrics_status?: string;
  trade_metrics_note?: string;
  trading_days?: number;
  total_trades?: number;
  beta?: number;
  alpha?: number;
  information_ratio?: number;
  sharpe_inference?: {
    sharpe_annualized?: number | null;
    psr?: number | null;
    dsr?: number | null;
    psr_significant?: boolean;
    dsr_significant?: boolean;
    ci_lower?: number | null;
    ci_upper?: number | null;
  };
  tracking_error?: number;
  var_95?: number;
  cvar_95?: number;
  max_drawdown_recovery_date?: string | null;
  max_drawdown_recovery_days?: number | null;
  metrics_status?: 'ok' | 'insufficient_data' | string;
  treynor_ratio?: number;
  excess_return?: number;
  excess_annualized?: number;
}

export interface BacktestNavDataStaleWarning {
  message?: string;
  [key: string]: unknown;
}

export interface BacktestNavQualityWarning {
  message?: string;
  funds?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface BacktestDataQualityFund {
  fund_code: string;
  coverage_ratio: number;
  total_trading_days: number;
  available_days: number;
  max_gap_days: number;
  spike_count: number;
  status: 'good' | 'warning' | 'poor' | string;
}

export interface BacktestDataQualityResponse {
  overall_status: 'good' | 'warning' | 'poor' | string;
  can_proceed: boolean;
  warnings: string[];
  funds: BacktestDataQualityFund[];
}

export interface BacktestResult {
  id: number;
  strategy_id: number;
  strategy_name: string | null;
  start_date: string;
  end_date: string;
  initial_capital: string | number;
  status: BacktestStatus;
  progress: number;
  progress_message?: string | null;
  metrics: BacktestMetrics | null;
  quality?: BacktestQuality | null;
  nav_data_stale?: BacktestNavDataStaleWarning | null;
  nav_quality_warning?: BacktestNavQualityWarning | null;
  error_msg: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface EquityPoint {
  trade_date: string;
  equity: number;
  cash: number;
  position_value: number;
  benchmark_value: number | null;
}

export interface EquityResponse {
  run_id: number;
  records: EquityPoint[];
}

export interface TradeRecord {
  trade_id: number;
  order_date: string;
  confirm_date: string | null;
  fund_code: string;
  direction: 'subscribe' | 'redeem';
  amount: number;
  shares: number | null;
  nav: number | null;
  fee: number;
}

export interface TradesResponse {
  run_id: number;
  items: TradeRecord[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface AttributionFamaFrench {
  alpha: number;
  beta_mkt: number;
  beta_smb: number;
  beta_hml: number;
  beta_rmw?: number;
  beta_cma?: number;
  r_squared: number;
}

export interface AttributionBrinson {
  allocation_effect: number;
  selection_effect: number;
  interaction_effect: number;
  total_excess: number;
}

export interface AttributionResponse {
  run_id: number;
  fama_french: AttributionFamaFrench | null;
  brinson: AttributionBrinson | null;
}

export interface BacktestProgressMessage {
  run_id?: number;
  type?: 'progress' | 'complete' | 'error';
  progress: number;
  message: string;
  status?: BacktestStatus;
}

export interface AIAttributionReportResponse {
  report_text: string;
  ai_generated_label: string;
  data_link: string;
  input_data: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

async function submitBacktest(payload: BacktestSubmitRequest): Promise<BacktestSubmitResponse> {
  const { data } = await apiClient.post<BacktestSubmitResponse>('/v1/backtests', payload);
  return data;
}

async function rerunBacktest(runId: number): Promise<BacktestSubmitResponse> {
  const { data } = await apiClient.post<BacktestSubmitResponse>(`/v1/backtests/${runId}/rerun`);
  return data;
}

async function checkBacktestDataQuality(payload: BacktestSubmitRequest): Promise<BacktestDataQualityResponse> {
  const { data } = await apiClient.post<BacktestDataQualityResponse>('/v1/backtests/check-quality', payload);
  return data;
}

async function fetchBacktestStatus(runId: number): Promise<BacktestResult> {
  const { data } = await apiClient.get<BacktestResult>(`/v1/backtests/${runId}`);
  return data;
}

async function fetchBacktestEquity(runId: number): Promise<EquityResponse> {
  const { data } = await apiClient.get<EquityResponse>(`/v1/backtests/${runId}/equity`);
  return data;
}

async function fetchBacktestTrades(
  runId: number,
  page: number = 1,
  pageSize: number = 20,
): Promise<TradesResponse> {
  const { data } = await apiClient.get<TradesResponse>(`/v1/backtests/${runId}/trades`, {
    params: { page, page_size: pageSize },
  });
  return data;
}

async function fetchBacktestAttribution(runId: number): Promise<AttributionResponse> {
  const { data } = await apiClient.get<AttributionResponse>(
    `/v1/backtests/${runId}/attribution`,
  );
  return data;
}

async function fetchAIAttributionReport(runId: number): Promise<AIAttributionReportResponse> {
  const { data } = await apiClient.post<AIAttributionReportResponse>(
    '/v1/ai/attribution-report',
    { run_id: runId },
  );
  return data;
}

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useBacktestList() {
  return useQuery({
    queryKey: ['backtest', 'list'],
    queryFn: async () => {
      const { data } = await apiClient.get<BacktestResult[]>('/v1/backtests');
      return data;
    },
    refetchInterval: (query) => {
      const items = query.state.data;
      if (items?.some((item) => item.status === 'pending' || item.status === 'running')) {
        return 5000;
      }
      return false;
    },
  });
}

export function useBacktestStatus(runId: number | undefined) {
  return useQuery({
    queryKey: ['backtest', 'status', runId],
    queryFn: () => fetchBacktestStatus(runId!),
    enabled: !!runId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      // Auto-refresh while pending/running
      if (status === 'pending' || status === 'running') {
        return 5000;
      }
      return false;
    },
  });
}

export function useBacktestDataQualityCheck(payload: BacktestSubmitRequest | null, enabled: boolean = true) {
  return useQuery({
    queryKey: ['backtest', 'quality-check', payload],
    queryFn: () => checkBacktestDataQuality(payload!),
    enabled: !!payload && enabled,
    staleTime: 60 * 1000,
  });
}

export function useBacktestEquity(runId: number | undefined, enabled: boolean = true) {
  return useQuery({
    queryKey: ['backtest', 'equity', runId],
    queryFn: () => fetchBacktestEquity(runId!),
    enabled: !!runId && enabled,
  });
}

export function useBacktestTrades(
  runId: number | undefined,
  page: number = 1,
  pageSize: number = 20,
  enabled: boolean = true,
) {
  return useQuery({
    queryKey: ['backtest', 'trades', runId, page, pageSize],
    queryFn: () => fetchBacktestTrades(runId!, page, pageSize),
    enabled: !!runId && enabled,
    placeholderData: (previousData) => previousData,
  });
}

export function useBacktestAttribution(runId: number | undefined, enabled: boolean = true) {
  return useQuery({
    queryKey: ['backtest', 'attribution', runId],
    queryFn: () => fetchBacktestAttribution(runId!),
    enabled: !!runId && enabled,
  });
}

export function useAIAttributionReport(runId: number | undefined) {
  return useMutation({
    mutationFn: () => fetchAIAttributionReport(runId!),
    mutationKey: ['backtest', 'ai-attribution', runId],
  });
}

// ---------------------------------------------------------------------------
// Mutation hooks
// ---------------------------------------------------------------------------

export function useSubmitBacktest() {
  return useMutation({
    mutationFn: submitBacktest,
  });
}

export function useDeleteBacktest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (runId: number) => {
      await apiClient.delete(`/v1/backtests/${runId}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['backtest', 'list'] });
    },
  });
}

export function useRerunBacktest() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: rerunBacktest,
    onSuccess: (_data, runId) => {
      queryClient.invalidateQueries({ queryKey: ['backtest', 'list'] });
      queryClient.invalidateQueries({ queryKey: ['backtest', 'status', runId] });
    },
  });
}

// ---------------------------------------------------------------------------
// WebSocket helper
// ---------------------------------------------------------------------------

/**
 * Creates a WebSocket connection for backtest progress updates.
 * Returns a cleanup function to close the connection.
 */
export function createBacktestProgressWs(
  runId: number,
  onMessage: (msg: BacktestProgressMessage) => void,
  onError?: (error: Event) => void,
  onClose?: () => void,
  onOpen?: () => void,
): () => void {
  const baseUrl = import.meta.env.VITE_WS_BASE_URL || 
    `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;
  const wsUrl = `${baseUrl}/api/v1/backtests/${runId}/progress`;

  const ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    onOpen?.();
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data) as BacktestProgressMessage;
      onMessage(data);
    } catch {
      // Ignore malformed messages
    }
  };

  ws.onerror = (event) => {
    onError?.(event);
  };

  ws.onclose = () => {
    onClose?.();
  };

  return () => {
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close();
    }
  };
}

/**
 * Hook to invalidate backtest queries (useful after WS complete message).
 */
export function useInvalidateBacktest() {
  const queryClient = useQueryClient();
  return (runId: number) => {
    queryClient.invalidateQueries({ queryKey: ['backtest', 'status', runId] });
    queryClient.invalidateQueries({ queryKey: ['backtest', 'equity', runId] });
    queryClient.invalidateQueries({ queryKey: ['backtest', 'trades', runId] });
    queryClient.invalidateQueries({ queryKey: ['backtest', 'attribution', runId] });
  };
}

// ---------------------------------------------------------------------------
// Rolling metrics
// ---------------------------------------------------------------------------

export interface RollingMetricsResponse {
  run_id: number;
  dates: string[];
  rolling_return: number[];
  rolling_sharpe: number[];
  rolling_drawdown: number[];
  rolling_volatility: number[];
  monthly_returns: Record<string, number>;
  yearly_returns: Record<string, number>;
}

async function fetchRollingMetrics(runId: number): Promise<RollingMetricsResponse> {
  const { data } = await apiClient.get<RollingMetricsResponse>(
    `/v1/backtests/${runId}/rolling`,
  );
  return data;
}

export function useBacktestRolling(runId: number | undefined, enabled: boolean = true) {
  return useQuery({
    queryKey: ['backtest', 'rolling', runId],
    queryFn: () => fetchRollingMetrics(runId!),
    enabled: !!runId && enabled,
  });
}

// ---------------------------------------------------------------------------
// Benchmark metrics
// ---------------------------------------------------------------------------

export interface BenchmarkMetricsResponse {
  run_id: number;
  benchmark_code: string | null;
  alpha: number | null;
  beta: number | null;
  information_ratio: number | null;
  tracking_error: number | null;
  treynor_ratio: number | null;
  excess_return: number | null;
  excess_annualized: number | null;
  var_95: number | null;
  cvar_95: number | null;
}

async function fetchBenchmarkMetrics(runId: number): Promise<BenchmarkMetricsResponse> {
  const { data } = await apiClient.get<BenchmarkMetricsResponse>(
    `/v1/backtests/${runId}/benchmark`,
  );
  return data;
}

export function useBacktestBenchmark(runId: number | undefined, enabled: boolean = true) {
  return useQuery({
    queryKey: ['backtest', 'benchmark', runId],
    queryFn: () => fetchBenchmarkMetrics(runId!),
    enabled: !!runId && enabled,
  });
}

// ---------------------------------------------------------------------------
// Walk-Forward validation
// ---------------------------------------------------------------------------

export interface WalkForwardWindow {
  window_id: number;
  train_start: string;
  train_end: string;
  test_start: string;
  test_end: string;
  is_sharpe: number;
  oos_sharpe: number;
  is_return: number;
  oos_return: number;
  is_max_drawdown: number;
  oos_max_drawdown: number;
}

export interface WalkForwardResponse {
  run_id: number;
  wfe: number;
  avg_oos_sharpe: number;
  avg_is_sharpe: number;
  avg_oos_return: number;
  oos_win_rate: number;
  total_oos_return: number;
  is_robust: boolean;
  windows: WalkForwardWindow[];
  note?: string | null;
}

export interface WalkForwardParams {
  train_months?: number;
  test_months?: number;
  step_months?: number;
}

async function fetchWalkForward(
  runId: number,
  params?: WalkForwardParams,
): Promise<WalkForwardResponse> {
  const { data } = await apiClient.get<WalkForwardResponse>(
    `/v1/backtests/${runId}/walk-forward`,
    { params: params || {} },
  );
  return data;
}

export function useBacktestWalkForward(
  runId: number | undefined,
  params?: WalkForwardParams,
  enabled: boolean = true,
) {
  return useQuery({
    queryKey: ['backtest', 'walk-forward', runId, params],
    queryFn: () => fetchWalkForward(runId!, params),
    enabled: !!runId && enabled,
  });
}
