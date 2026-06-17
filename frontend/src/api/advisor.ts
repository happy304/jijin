/**
 * Advisor / 组合检查 API hooks using TanStack Query (v3).
 *
 * Provides hooks for:
 * - Fund analysis with portfolio check conclusions (increase/decrease/watch)
 * - Portfolio rebalancing references
 * - Historical signal queries
 * - Advisor configuration
 * - Advisor backtest validation
 *
 * v3 changes:
 * - Added market regime detection (bull/bear/crisis/volatile/normal)
 * - Added correlation control (high-corr funds deduplicated)
 * - Added signal cooldown mechanism
 * - Updated config response with new v3 features
 * - Added advisor backtest hook
 */

import { useQuery, useMutation } from '@tanstack/react-query';
import apiClient from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type RiskLevel = 'conservative' | 'moderate' | 'aggressive';
export type ActionType = 'buy' | 'sell' | 'hold' | 'watch';
export type SupportActionType = 'increase_watch' | 'reduce_watch' | 'hold_review' | 'risk_alert' | 'consider_increase' | 'consider_reduce' | 'observe' | 'review_required';
export type AdviceStrength = 'weak' | 'medium' | 'strong';
export type UrgencyLevel = 'high' | 'normal' | 'low';
export type InvestmentGoal = 'cash_management' | 'stable_growth' | 'balanced' | 'long_term_growth';
export type InvestmentHorizon = 'within_3_months' | '3_to_12_months' | '1_to_3_years' | 'over_3_years';
export type LiquidityNeed = 'high' | 'medium' | 'low';
export type ExecutionStatus = 'planned' | 'executed' | 'partial' | 'not_executed';
export type TradeIntent = 'subscribe' | 'redeem' | 'hold';

export interface TechnicalIndicatorsResponse {
  ma5: number | null;
  ma20: number | null;
  ma60: number | null;
  macd_signal: string;
  rsi_14: number | null;
  rsi_signal: string;
  boll_position: number | null;
  trend_score: number;
}

export interface MomentumAnalysisResponse {
  return_5d: number | null;
  return_20d: number | null;
  return_60d: number | null;
  zscore_20d: number | null;
  current_vol: number | null;
  vol_percentile: number | null;
  regime: string;
}

export interface RiskPositionResponse {
  annualized_vol: number;
  max_drawdown_1y: number;
  risk_budget_pct: number;
  suggested_position_pct: number;
  suggested_amount: number;
}

export interface PredictionResponse {
  expected_return_30d: number | null;
  expected_return_90d: number | null;
  prob_positive_30d: number | null;
  prob_positive_90d: number | null;
  var_95_30d: number | null;
  cvar_95_30d: number | null;
  confidence_band_width: number | null;
  sample_size: number;
  note: string;
}

export interface FeeEstimateResponse {
  subscribe_fee_rate: number;
  redeem_fee_rate: number;
  estimated_fee: number;
  fee_impact_pct: number;
  net_trade_amount?: number | null;
  fee_source?: string | null;
}

export interface ScoresResponse {
  technical: number;
  momentum: number;
  strategy: number;
  prediction: number;
  cross_sectional: number;
  composite: number;
}

export interface ReasonFactorResponse {
  name: string;
  impact: 'positive' | 'negative' | 'neutral';
  score?: number | null;
  explanation: string;
}

export interface AdviceReasoningResponse {
  summary: string;
  confidence_level: 'high' | 'medium' | 'low';
  factors: ReasonFactorResponse[];
}

export interface TradePlanTriggerResponse {
  trigger_type: 'pause_buy' | 'stop_buy' | 'reduce_position' | 'review' | 'refresh' | string;
  condition: string;
  action: string;
  reason: string;
  severity: 'info' | 'warning' | 'high' | string;
}

export interface AdvisorRiskConstraintResponse {
  status: 'passed' | 'adjusted' | 'blocked' | string;
  constraints: Record<string, number>;
  violations: Array<{
    code: string;
    severity: 'info' | 'warning' | 'high' | string;
    message: string;
    limit?: number;
    actual?: number;
  }>;
  original_suggested_amount: number;
  adjusted_suggested_amount: number;
  blocked_actions: string[];
}

export interface TradePlanResponse {
  execution_type: 'one_time' | 'batch' | 'fixed_investment' | 'hold';
  suggested_amount: number;
  min_amount: number;
  max_amount: number;
  current_weight: number;
  target_weight: number;
  batch_count?: number | null;
  batch_interval_days?: number | null;
  explanation: string;
  triggers?: TradePlanTriggerResponse[];
}

export interface PortfolioImpactResponse {
  before_weight: number;
  after_weight: number;
  position_change: number;
  risk_change: 'increase' | 'decrease' | 'unchanged';
  concentration_warning?: string | null;
  explanation: string;
}

export interface SuitabilityResponse {
  user_risk_level: string;
  fund_risk_level: string;
  matched: boolean;
  action_adjusted: boolean;
  warning?: string | null;
}

export interface AdviceValidityResponse {
  generated_at: string;
  data_as_of: string;
  valid_until: string;
  invalidation_rules: string[];
}

export interface ProfileConstraintResponse {
  name: string;
  triggered: boolean;
  effect: 'none' | 'reduce_amount' | 'hold' | 'warning';
  explanation: string;
}

export interface ReliabilityAdjustmentResponse {
  status: 'healthy' | 'degraded' | 'unhealthy' | 'insufficient_data' | 'unknown' | 'not_evaluated' | string;
  multiplier: number;
  confidence_multiplier: number;
  amount_multiplier: number;
  reason: string;
  metrics: Record<string, unknown> & {
    oos_selection_source?: 'exact' | 'moderate_fallback' | 'latest_fallback' | string;
    oos_requested_risk_level?: string;
    oos_risk_level?: string;
    oos_avg_ic?: number;
    oos_ic_degradation?: number;
    oos_total_signals?: number;
    oos_buy_hit_rate?: number;
    oos_sell_hit_rate?: number;
    oos_pbo?: number;
    oos_cpcv_n_paths?: number;
    oos_cpcv_avg_oos_sharpe?: number;
    oos_cpcv_std_oos_sharpe?: number;
    oos_cpcv_avg_is_sharpe?: number;
    oos_snapshot_date?: string;
    oos_config_hash?: string;
    oos_data_version?: string;
    oos_validation_window?: string;
    oos_updated_at?: string;
  };
}

export interface AdvisorDataQualityResponse {
  status: 'good' | 'warning' | 'poor' | 'unknown' | string;
  score: number;
  nav_count: number;
  data_start?: string | null;
  data_end?: string | null;
  sample_sufficient: boolean;
  prediction_sample_size: number;
  coverage_ratio?: number | null;
  max_gap_days?: number;
  spike_count?: number;
  spike_dates?: string[];
  freshness_days?: number | null;
  warnings: string[];
  current_volatility?: number | null;
  volatility_percentile?: number | null;
  source_consistency?: {
    point_count?: number;
    source_count?: number;
    primary_source?: string | null;
    source_switch_count?: number;
    source_switch_ratio?: number;
    missing_source_count?: number;
    sources?: Record<string, number>;
  };
  adjustment_consistency?: {
    point_count?: number;
    adjusted_count?: number;
    unit_nav_count?: number;
    fallback_to_unit_count?: number;
    adjusted_coverage_ratio?: number | null;
    factor_jump_count?: number;
    factor_jump_dates?: string[];
    missing_unit_count?: number;
    missing_adj_count?: number;
  };
}

export interface AdvisorOverfitRiskResponse {
  level: 'low' | 'medium' | 'high' | 'unknown' | string;
  score: number;
  pbo?: number | null;
  cpcv_n_paths?: number;
  cpcv_avg_oos_sharpe?: number | null;
  cpcv_std_oos_sharpe?: number | null;
  cpcv_avg_is_sharpe?: number | null;
  oos_ic?: number | null;
  ic_degradation?: number | null;
  oos_signal_count: number;
  engine_health_status?: string | null;
  rolling_ic_samples: number;
  reasons: string[];
  gate_action: 'allow' | 'reduce' | 'hold' | string;
}

export interface SignalContributionResponse {
  source: string;
  score: number;
  weight: number;
  contribution: number;
  available: boolean;
}

export interface DominantSignalResponse {
  source: string;
  contribution_share: number;
  single_signal_dominant: boolean;
}

export interface DecisionAuditResponse {
  effective_buy_threshold: number;
  effective_sell_threshold: number;
  threshold_state: 'above_buy_threshold' | 'below_sell_threshold' | 'within_hold_band' | string;
  threshold_margin: number;
  missing_sources: number;
  signal_weights: Record<string, number>;
  signal_availability: Record<string, boolean>;
  signal_contributions?: SignalContributionResponse[];
  dominant_signal?: DominantSignalResponse | null;
  data_quality: Record<string, unknown>;
  overfit_risk?: Record<string, unknown>;
  market_regime?: Record<string, unknown> | null;
  notes: string[];
}

export interface TradeTimingResponse {
  request_time?: string;
  timezone?: string;
  cutoff_time?: string;
  is_trading_day?: boolean;
  is_after_cutoff?: boolean;
  accepted_trade_date?: string;
  nav_date?: string;
  expected_confirm_date?: string | null;
  expected_settlement_date?: string | null;
  expected_available_date?: string | null;
  fund_type?: string | null;
  trade_intent?: TradeIntent;
  rule_basis?: string;
  calendar_source?: string;
  warnings?: string[];
}

export interface TradingAdviceItem {
  fund_code: string;
  fund_name: string | null;
  fund_type: string | null;
  advice_date: string;
  action: ActionType;
  support_action?: SupportActionType | string;
  support_label?: string;
  decision_support_only?: boolean;
  not_investment_advice_disclaimer?: string;
  confidence_calibration_status?: string;
  oos_validation_status?: string;
  worst_case_note?: string | null;
  strength?: AdviceStrength | string;
  trade_intent?: TradeIntent;
  confidence: number;
  urgency: UrgencyLevel;
  suggested_amount: number;
  suggested_shares?: number | null;
  estimated_gross_amount?: number | null;
  estimated_net_amount?: number | null;
  suggested_pct: number;
  position_after: number;
  trade_amount_min?: number;
  trade_amount_max?: number;
  execution_notes?: string[];
  scores: ScoresResponse;
  reasons: string[];
  risk_warnings: string[];
  limitations: string[];
  data_quality?: AdvisorDataQualityResponse;
  overfit_risk?: AdvisorOverfitRiskResponse;
  risk_constraints?: AdvisorRiskConstraintResponse;
  reasoning?: AdviceReasoningResponse;
  trade_plan?: TradePlanResponse;
  portfolio_impact?: PortfolioImpactResponse;
  suitability?: SuitabilityResponse;
  profile_constraints?: ProfileConstraintResponse[];
  reliability_adjustment?: ReliabilityAdjustmentResponse;
  validity?: AdviceValidityResponse;
  decision_audit?: DecisionAuditResponse;
  fee_estimate?: FeeEstimateResponse;
  trade_timing?: TradeTimingResponse;
  technical_indicators?: TechnicalIndicatorsResponse;
  momentum_analysis?: MomentumAnalysisResponse;
  risk_position?: RiskPositionResponse;
  prediction?: PredictionResponse;
}

export interface AdvisorSummary {
  buy_count: number;
  sell_count: number;
  hold_count: number;
  watch_count?: number;
  total_buy_amount: number;
  total_sell_amount: number;
  high_confidence_signals: number;
  top_buy: string | null;
  top_sell: string | null;
}

export interface AdvisorRiskComparisonItem {
  risk_level: RiskLevel;
  fund_count: number;
  advices: TradingAdviceItem[];
  summary: AdvisorSummary;
  execution_context?: AdvisorExecutionContextResponse | null;
}

export interface AdvisorAnalyzeResponse {
  advice_date: string;
  total_capital: number;
  risk_level: RiskLevel;
  fund_count: number;
  advices: TradingAdviceItem[];
  summary: AdvisorSummary;
  user_profile?: Record<string, unknown> | null;
  risk_comparison?: Record<RiskLevel, AdvisorRiskComparisonItem> | null;
  execution_context?: AdvisorExecutionContextResponse | null;
  trading_time?: TradeTimingResponse & {
    // 兼容旧字段
    effective_date: string;
    cutoff_info: string;
    note: string;
  };
  disclaimer: string;
}

export interface PortfolioAdviceResponse extends AdvisorAnalyzeResponse {
  strategy_id: number;
  strategy_name: string;
  strategy_type: string;
}

export interface PositionDetailPayload {
  market_value?: number;
  shares?: number;
  cost_basis?: number;
  buy_date?: string;
  // 兼容旧字段
  amount?: number;
  cost?: number;
}

export interface AdvisorAnalyzeRequest {
  fund_codes: string[];
  total_capital?: number;
  current_positions?: Record<string, number>;
  positions_detail?: Record<string, PositionDetailPayload>;
  risk_level?: RiskLevel;
  investment_goal?: InvestmentGoal;
  investment_horizon?: InvestmentHorizon;
  liquidity_need?: LiquidityNeed;
  max_drawdown_tolerance?: number;
  monthly_invest_amount?: number;
  industry_concentration_tolerance?: 'low' | 'medium' | 'high';
  qdii_fx_risk_tolerance?: 'low' | 'medium' | 'high';
  fee_sensitivity?: 'low' | 'medium' | 'high';
  compare_risk_levels?: boolean;
}

export interface PortfolioAdviceRequest {
  strategy_id: number;
  total_capital?: number;
  current_positions?: Record<string, number>;
  positions_detail?: Record<string, PositionDetailPayload>;
  risk_level?: RiskLevel;
  investment_goal?: InvestmentGoal;
  investment_horizon?: InvestmentHorizon;
  liquidity_need?: LiquidityNeed;
  max_drawdown_tolerance?: number;
  monthly_invest_amount?: number;
  industry_concentration_tolerance?: 'low' | 'medium' | 'high';
  qdii_fx_risk_tolerance?: 'low' | 'medium' | 'high';
  fee_sensitivity?: 'low' | 'medium' | 'high';
  compare_risk_levels?: boolean;
}

export interface SignalItem {
  id: number;
  strategy_id: number;
  strategy_name: string;
  fund_code: string;
  signal_date: string;
  direction: string;
  strength: number | null;
  target_weight: number | null;
  amount: number | null;
  shares: number | null;
  reason: string | null;
  created_at: string | null;
}

export interface SignalListResponse {
  items: SignalItem[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface SignalQueryParams {
  fund_code?: string;
  strategy_id?: number;
  direction?: string;
  start_date?: string;
  end_date?: string;
  page?: number;
  page_size?: number;
}

export interface RiskProfileConfig {
  label: string;
  description: string;
  buy_threshold: number;
  sell_threshold: number;
  max_single_position: number;
  max_daily_trade_pct: number;
  target_portfolio_vol: number;
}

export interface AdvisorConfigResponse {
  version: string;
  risk_profiles: Record<RiskLevel, RiskProfileConfig>;
  scoring_dimensions: Record<string, { description: string; note?: string }>;
  position_sizing: { method: string; description: string };
  market_regime?: { description: string; regimes: string[]; note: string };
  signal_cooldown?: { enabled: boolean; cooldown_days: number; description: string };
  correlation_control?: { enabled: boolean; threshold: number; description: string };
  fee_estimation: { enabled: boolean; description: string };
  fund_type_awareness: { description: string; types: string[] };
  anti_overfitting?: {
    reliability_adjustment?: string;
    oos_reliability_layer?: string;
    oos_auto_refresh?: string;
    oos_risk_level_reuse?: string;
    learned_parameter_shrinkage?: string;
    parameter_release_gate?: string;
    shadow_mode?: string;
    weight_change_limit?: string;
    threshold_change_limit?: string;
    oos_signal_minimum?: string;
  };
  limitations: string[];
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

async function analyzeFunds(payload: AdvisorAnalyzeRequest): Promise<AdvisorAnalyzeResponse> {
  const { data } = await apiClient.post<AdvisorAnalyzeResponse>('/v1/advisor/analyze', payload);
  return data;
}

async function getPortfolioAdvice(payload: PortfolioAdviceRequest): Promise<PortfolioAdviceResponse> {
  const { data } = await apiClient.post<PortfolioAdviceResponse>('/v1/advisor/portfolio', payload);
  return data;
}

async function fetchSignals(params?: SignalQueryParams): Promise<SignalListResponse> {
  const { data } = await apiClient.get<SignalListResponse>('/v1/advisor/signals', { params });
  return data;
}

async function fetchAdvisorConfig(): Promise<AdvisorConfigResponse> {
  const { data } = await apiClient.get<AdvisorConfigResponse>('/v1/advisor/config');
  return data;
}

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useAnalyzeFunds() {
  return useMutation({
    mutationFn: analyzeFunds,
  });
}

export function usePortfolioAdvice() {
  return useMutation({
    mutationFn: getPortfolioAdvice,
  });
}

export function useSignals(params?: SignalQueryParams) {
  return useQuery({
    queryKey: ['advisor-signals', params],
    queryFn: () => fetchSignals(params),
  });
}

export function useAdvisorConfig() {
  return useQuery({
    queryKey: ['advisor-config'],
    queryFn: fetchAdvisorConfig,
    staleTime: 60 * 60 * 1000, // 配置很少变化，缓存1小时
  });
}

// ---------------------------------------------------------------------------
// 建议保存与历史查询
// ---------------------------------------------------------------------------

export interface SaveAdvisorResultRequest {
  advice_date: string;
  fund_codes: string[];
  total_capital: number;
  risk_level: string;
  strategy_id?: number | null;
  strategy_name?: string | null;
  current_positions?: Record<string, number> | null;
  positions_detail?: Record<string, PositionDetailPayload> | null;
  user_profile?: Record<string, unknown> | null;
  advices: Record<string, unknown>[];
  summary: Record<string, unknown>;
  note?: string | null;
}

export interface AdvisorNavDataStaleWarning {
  message?: string;
  [key: string]: unknown;
}

export interface AdvisorNavQualityWarning {
  message?: string;
  funds?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface AdvisorHistoryItem {
  id: number;
  advice_date: string;
  fund_codes: string[];
  total_capital: number;
  risk_level: string;
  strategy_id: number | null;
  strategy_name: string | null;
  summary: AdvisorSummary;
  nav_data_stale?: AdvisorNavDataStaleWarning | null;
  nav_quality_warning?: AdvisorNavQualityWarning | null;
  note: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AdvisorHistoryListResponse {
  items: AdvisorHistoryItem[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface AdvisorExecutionContextResponse {
  analysis_mode?: string | null;
  requested_as_of_date?: string | null;
  resolved_risk_level?: string | null;
  data_sources?: {
    nav_by_fund?: Record<string, Record<string, unknown>>;
    signals_by_fund?: Record<string, Record<string, unknown>>;
    rules_by_fund?: Record<string, Record<string, unknown>>;
    macro_cutoff?: Record<string, unknown>;
    oos_by_fund?: Record<string, Record<string, unknown>>;
  };
  data_quality_warnings?: string[];
  data_trust?: {
    score: number;
    level: 'high' | 'medium' | 'low' | string;
    stale_funds: string[];
    missing_oos_snapshot_funds: string[];
    warnings: string[];
  };
  oos_context?: Record<string, unknown>;
  learned_params?: Record<string, unknown> | null;
  engine_health?: Record<string, unknown> | null;
  runtime_health?: Record<string, unknown> | null;
  replay?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface AdvisorExecutionRecord {
  id: number;
  advisor_result_id: number;
  advice_date: string | null;
  fund_code: string;
  advice_action: ActionType;
  trade_intent: TradeIntent;
  suggested_amount: number | null;
  suggested_shares: number | null;
  suggested_pct: number | null;
  confidence: number | null;
  execution_status: ExecutionStatus;
  executed_date: string | null;
  executed_amount: number | null;
  executed_shares: number | null;
  executed_nav: number | null;
  executed_fee: number | null;
  execution_channel: string | null;
  not_executed_reason: string | null;
  deviation_reason: string | null;
  user_note: string | null;
  source: string;
  metadata: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ExecutionFundSummary {
  fund_code: string;
  record_count: number;
  statuses: ExecutionStatus[];
  adopted: boolean;
  latest_status: ExecutionStatus | 'no_record' | string;
  latest_executed_date: string | null;
  total_executed_amount: number;
  total_executed_shares: number;
  suggested_amount: number | null;
  suggested_shares: number | null;
  amount_execution_ratio: number | null;
  amount_deviation_pct: number | null;
  drift_level: 'aligned' | 'moderate_deviation' | 'large_deviation' | 'adopted_without_amount' | 'unknown' | string;
  not_executed_reasons: string[];
  deviation_reasons: string[];
  advice_action?: ActionType;
  trade_intent?: TradeIntent;
}

export interface AdvisorExecutionSummary {
  status: string;
  actionable_advice_count: number;
  record_count: number;
  recorded_actionable_count: number;
  adopted_count: number;
  adoption_rate: number | null;
  status_counts: Record<ExecutionStatus, number>;
  avg_abs_amount_deviation_pct: number | null;
  significant_deviation_count: number;
  by_fund: Record<string, ExecutionFundSummary>;
  interpretation: string;
}

export type ExecutionPlanTaskStatus = 'pending' | 'done' | 'skipped';

export interface ExecutionPlanTaskItem {
  task_key: string;
  title: string;
  scheduled_date: string;
  amount_min: number | null;
  amount_max: number | null;
  description: string;
  trigger_summary?: string | null;
  index: number;
  execution_type: 'one_time' | 'batch' | 'fixed_investment' | 'hold' | string;
  status: ExecutionPlanTaskStatus;
  matched_execution_id?: number | null;
  matched_execution_status?: ExecutionStatus | null;
  matched_executed_date?: string | null;
  matched_record_count: number;
}

export interface ExecutionPlanFundStatus {
  fund_code: string;
  tasks: ExecutionPlanTaskItem[];
  pending_count: number;
  done_count: number;
  skipped_count: number;
}

export interface AdvisorExecutionPlanStatus {
  by_fund: Record<string, ExecutionPlanFundStatus>;
  summary: {
    task_count: number;
    pending_count: number;
    done_count: number;
    skipped_count: number;
  };
}

export interface AdvisorExecutionImportRowResult {
  row_number: number;
  status: 'created' | 'failed' | string;
  fund_code?: string | null;
  execution_status?: ExecutionStatus | string;
  error?: string;
}

export interface AdvisorExecutionImportResponse {
  status: 'completed' | 'partial' | string;
  advisor_result_id: number;
  filename: string;
  total_rows: number;
  created_count: number;
  failed_count: number;
  rows: AdvisorExecutionImportRowResult[];
  records: AdvisorExecutionRecord[];
  summary: AdvisorExecutionSummary;
}

export interface AdvisorHoldingImportPosition {
  fund_code: string;
  market_value: number;
  shares: number;
  cost_basis: number;
  buy_date: string | null;
}

export interface AdvisorPositionsResponse {
  status: 'success' | 'saved' | 'unavailable' | string;
  total: number;
  positions: AdvisorHoldingImportPosition[];
}

export interface AdvisorHoldingImportRowResult {
  row_number: number;
  status: 'created' | 'failed' | string;
  fund_code?: string | null;
  error?: string | null;
}

export interface AdvisorPositionImportGovernanceSummary {
  position_count: number;
  imported_row_count: number;
  total_market_value: number;
  total_cost_basis: number;
  duplicate_fund_codes: string[];
  zero_value_fund_codes: string[];
  suspicious_cost_fund_codes: string[];
  warnings: string[];
}

export interface AdvisorHoldingImportResponse {
  status: 'completed' | 'partial' | string;
  filename: string;
  total_rows: number;
  imported_count: number;
  failed_count: number;
  positions: AdvisorHoldingImportPosition[];
  rows: AdvisorHoldingImportRowResult[];
  governance_summary: AdvisorPositionImportGovernanceSummary;
}

export interface AdvisorPositionImportHistoryItem {
  id: number;
  filename: string;
  file_format: string;
  status: 'completed' | 'partial' | 'failed' | string;
  total_rows: number;
  imported_count: number;
  failed_count: number;
  replaced_position_count: number;
  rows: AdvisorHoldingImportRowResult[];
  positions: AdvisorHoldingImportPosition[];
  metadata: (Record<string, unknown> & { governance_summary?: AdvisorPositionImportGovernanceSummary }) | null;
  created_at: string | null;
}

export interface AdvisorPositionImportHistoryResponse {
  items: AdvisorPositionImportHistoryItem[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface AdvisorPositionImportRestoreResponse {
  status: 'restored' | string;
  total: number;
  positions: AdvisorHoldingImportPosition[];
  restored_from: AdvisorPositionImportHistoryItem;
}

export interface SnapshotVersionResponse {
  version_id: string;
  provider: string;
  fund_code: string;
  endpoint: string;
  ext: string;
  snapshot_date: string;
  captured_at?: string | null;
  sha256: string;
  size_bytes: number;
}

export interface SnapshotVersionListResponse {
  items: SnapshotVersionResponse[];
  total: number;
}

export interface SnapshotVersionQueryParams {
  provider?: string;
  fund_code?: string;
  endpoint?: string;
  ext?: string;
  snapshot_date?: string;
  as_of?: string;
  limit?: number;
}

export interface AdvisorReminder {
  id: number;
  advisor_result_id: number;
  fund_code: string | null;
  category: 'validity' | 'risk' | 'execution' | 'plan' | 'system' | string;
  reminder_type: string;
  severity: 'info' | 'warning' | 'error' | 'success' | string;
  status: 'active' | 'resolved' | 'dismissed' | string;
  title: string;
  description: string;
  payload: Record<string, unknown> | null;
  trigger_date: string | null;
  resolved_at: string | null;
  dismissed_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AdvisorReminderListResponse {
  items: AdvisorReminder[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export type AdvisorReminderSeverity = 'info' | 'warning' | 'error' | 'success' | string;
export type AdvisorReminderCategory = 'validity' | 'risk' | 'execution' | 'plan' | 'system' | string;
export type AdvisorReminderChannel = 'email' | 'wecom' | 'telegram' | string;

export interface AdvisorReminderPreference {
  id?: number;
  profile_key: string;
  enabled: boolean;
  min_severity: AdvisorReminderSeverity;
  lookahead_days: number;
  channels: AdvisorReminderChannel[] | null;
  muted_categories: AdvisorReminderCategory[];
  quiet_hours: Record<string, unknown> | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface AdvisorReminderPreferenceRequest {
  enabled: boolean;
  min_severity: AdvisorReminderSeverity;
  lookahead_days: number;
  channels: AdvisorReminderChannel[] | null;
  muted_categories: AdvisorReminderCategory[];
  quiet_hours: Record<string, unknown> | null;
}

export interface AdvisorReminderPreferenceResponse {
  status: string;
  preference: AdvisorReminderPreference;
}

export interface AdvisorReminderDigestResponse {
  status: string;
  preference?: AdvisorReminderPreference;
  digest: Record<string, unknown> | null;
  message: string;
  notification: {
    total: number;
    sent: number;
    failed: number;
    channels_used: string[];
    errors: Record<string, string[]>;
  };
}

export interface AdvisorHistoryDetailResponse {
  id: number;
  advice_date: string;
  fund_codes: string[];
  total_capital: number;
  risk_level: string;
  strategy_id: number | null;
  strategy_name: string | null;
  current_positions: Record<string, number> | null;
  positions_detail: Record<string, PositionDetailPayload> | null;
  user_profile: Record<string, unknown> | null;
  advices: TradingAdviceItem[];
  summary: AdvisorSummary;
  nav_data_stale?: AdvisorNavDataStaleWarning | null;
  nav_quality_warning?: AdvisorNavQualityWarning | null;
  note: string | null;
  analysis_mode?: string | null;
  source_result_id?: number | null;
  learned_params_version_id?: number | null;
  execution_context?: AdvisorExecutionContextResponse | null;
  execution_records?: AdvisorExecutionRecord[];
  execution_summary?: AdvisorExecutionSummary;
  execution_plan_status?: AdvisorExecutionPlanStatus;
  reminders?: AdvisorReminder[];
  risk_comparison?: Record<RiskLevel, AdvisorRiskComparisonItem> | null;
  created_at: string | null;
  updated_at: string | null;
}

async function saveAdvisorResult(payload: SaveAdvisorResultRequest): Promise<{ status: string; id: number; message: string }> {
  const { data } = await apiClient.post<{ status: string; id: number; message: string }>('/v1/advisor/save', payload);
  return data;
}

async function fetchAdvisorHistory(params?: { page?: number; page_size?: number }): Promise<AdvisorHistoryListResponse> {
  const { data } = await apiClient.get<AdvisorHistoryListResponse>('/v1/advisor/history', { params });
  return data;
}

async function fetchAdvisorHistoryDetail(id: number): Promise<AdvisorHistoryDetailResponse> {
  const { data } = await apiClient.get<AdvisorHistoryDetailResponse>(`/v1/advisor/history/${id}`);
  return data;
}

async function deleteAdvisorHistory(id: number): Promise<{ status: string; message: string }> {
  const { data } = await apiClient.delete<{ status: string; message: string }>(`/v1/advisor/history/${id}`);
  return data;
}

async function refreshAdvisorHistory(id: number): Promise<{ status: string; id: number; source_id?: number; message: string; updated_at: string | null }> {
  const { data } = await apiClient.post<{ status: string; id: number; source_id?: number; message: string; updated_at: string | null }>(`/v1/advisor/history/${id}/refresh`);
  return data;
}

async function fetchAdvisorReminders(params?: { status?: string; category?: string; severity?: string; advisor_result_id?: number; page?: number; page_size?: number }): Promise<AdvisorReminderListResponse> {
  const { data } = await apiClient.get<AdvisorReminderListResponse>('/v1/advisor/reminders', { params });
  return data;
}

async function refreshAdvisorReminders(params?: { advisor_result_id?: number; lookback_days?: number; limit?: number }): Promise<{ status: string; processed: number; created: number; reactivated: number; updated: number; resolved: number; items: Array<Record<string, unknown>> }> {
  const { data } = await apiClient.post<{ status: string; processed: number; created: number; reactivated: number; updated: number; resolved: number; items: Array<Record<string, unknown>> }>('/v1/advisor/reminders/refresh', null, { params });
  return data;
}

async function updateAdvisorReminder(payload: { reminderId: number; status: 'active' | 'resolved' | 'dismissed' | string }): Promise<{ status: string; item: AdvisorReminder }> {
  const { data } = await apiClient.patch<{ status: string; item: AdvisorReminder }>(`/v1/advisor/reminders/${payload.reminderId}`, { status: payload.status });
  return data;
}

async function fetchAdvisorReminderPreference(profileKey?: string): Promise<AdvisorReminderPreferenceResponse> {
  const { data } = await apiClient.get<AdvisorReminderPreferenceResponse>('/v1/advisor/reminders/preferences', {
    params: profileKey ? { profile_key: profileKey } : undefined,
  });
  return data;
}

async function updateAdvisorReminderPreference(payload: { profileKey?: string; preference: AdvisorReminderPreferenceRequest }): Promise<AdvisorReminderPreferenceResponse> {
  const { data } = await apiClient.put<AdvisorReminderPreferenceResponse>('/v1/advisor/reminders/preferences', payload.preference, {
    params: payload.profileKey ? { profile_key: payload.profileKey } : undefined,
  });
  return data;
}

async function createAdvisorReminderDigest(params?: { dry_run?: boolean; days?: number; min_severity?: string; channels?: string; profile_key?: string; use_preferences?: boolean; limit?: number }): Promise<AdvisorReminderDigestResponse> {
  const { data } = await apiClient.post<AdvisorReminderDigestResponse>('/v1/advisor/reminders/digest', null, { params });
  return data;
}

export interface AdvisorExecutionRecordRequest {
  fund_code: string;
  execution_status: ExecutionStatus;
  advice_action?: ActionType | null;
  trade_intent?: TradeIntent | null;
  executed_date?: string | null;
  executed_amount?: number | null;
  executed_shares?: number | null;
  executed_nav?: number | null;
  executed_fee?: number | null;
  execution_channel?: string | null;
  not_executed_reason?: string | null;
  deviation_reason?: string | null;
  user_note?: string | null;
  source?: 'manual' | 'import' | 'api' | string;
  metadata?: Record<string, unknown> | null;
}

export interface AdvisorExecutionRecordUpdateRequest {
  execution_status?: ExecutionStatus | null;
  executed_date?: string | null;
  executed_amount?: number | null;
  executed_shares?: number | null;
  executed_nav?: number | null;
  executed_fee?: number | null;
  execution_channel?: string | null;
  not_executed_reason?: string | null;
  deviation_reason?: string | null;
  user_note?: string | null;
  metadata?: Record<string, unknown> | null;
}

async function createAdvisorExecutionRecord(payload: { resultId: number; record: AdvisorExecutionRecordRequest }): Promise<{ status: string; record: AdvisorExecutionRecord }> {
  const { data } = await apiClient.post<{ status: string; record: AdvisorExecutionRecord }>(`/v1/advisor/history/${payload.resultId}/executions`, payload.record);
  return data;
}

async function updateAdvisorExecutionRecord(payload: { executionId: number; record: AdvisorExecutionRecordUpdateRequest }): Promise<{ status: string; record: AdvisorExecutionRecord }> {
  const { data } = await apiClient.patch<{ status: string; record: AdvisorExecutionRecord }>(`/v1/advisor/executions/${payload.executionId}`, payload.record);
  return data;
}

async function deleteAdvisorExecutionRecord(executionId: number): Promise<{ status: string; message: string }> {
  const { data } = await apiClient.delete<{ status: string; message: string }>(`/v1/advisor/executions/${executionId}`);
  return data;
}

async function importAdvisorExecutionRecords(payload: { resultId: number; file: File }): Promise<AdvisorExecutionImportResponse> {
  const formData = new FormData();
  formData.append('file', payload.file);
  const { data } = await apiClient.post<AdvisorExecutionImportResponse>(`/v1/advisor/history/${payload.resultId}/executions/import`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return data;
}

async function fetchAdvisorPositions(): Promise<AdvisorPositionsResponse> {
  const { data } = await apiClient.get<AdvisorPositionsResponse>('/v1/advisor/positions');
  return data;
}

async function downloadAdvisorPositionsTemplate(format: 'csv' | 'xlsx' = 'csv'): Promise<Blob> {
  const { data } = await apiClient.get('/v1/advisor/positions/template', {
    params: { format },
    responseType: 'blob',
  });
  return data as Blob;
}

async function fetchAdvisorPositionImportHistory(params?: { page?: number; page_size?: number; limit?: number }): Promise<AdvisorPositionImportHistoryResponse> {
  const { data } = await apiClient.get<AdvisorPositionImportHistoryResponse>('/v1/advisor/positions/import-history', {
    params,
  });
  return data;
}

async function restoreAdvisorPositionsFromImportHistory(importId: number): Promise<AdvisorPositionImportRestoreResponse> {
  const { data } = await apiClient.post<AdvisorPositionImportRestoreResponse>(`/v1/advisor/positions/import-history/${importId}/restore`);
  return data;
}

async function replaceAdvisorPositions(payload: { positions: AdvisorHoldingImportPosition[] }): Promise<AdvisorPositionsResponse> {
  const { data } = await apiClient.put<AdvisorPositionsResponse>('/v1/advisor/positions', payload);
  return data;
}

async function importAdvisorPositions(file: File): Promise<AdvisorHoldingImportResponse> {
  const formData = new FormData();
  formData.append('file', file);
  const { data } = await apiClient.post<AdvisorHoldingImportResponse>('/v1/advisor/positions/import', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return data;
}

async function fetchSnapshotVersions(params?: SnapshotVersionQueryParams): Promise<SnapshotVersionListResponse> {
  const { data } = await apiClient.get<SnapshotVersionListResponse>('/v1/advisor/snapshots/versions', { params });
  return data;
}

async function downloadSnapshotVersion(versionId: string): Promise<Blob> {
  const { data } = await apiClient.get(`/v1/advisor/snapshots/versions/${versionId}`, {
    responseType: 'blob',
  });
  return data as Blob;
}

export function useSaveAdvisorResult() {
  return useMutation({
    mutationFn: saveAdvisorResult,
  });
}

export function useAdvisorHistory(params?: { page?: number; page_size?: number }) {
  return useQuery({
    queryKey: ['advisor-history', params],
    queryFn: () => fetchAdvisorHistory(params),
  });
}

export function useAdvisorHistoryDetail(id: number | null) {
  return useQuery({
    queryKey: ['advisor-history-detail', id],
    queryFn: () => fetchAdvisorHistoryDetail(id!),
    enabled: id != null,
  });
}

export function useDeleteAdvisorHistory() {
  return useMutation({
    mutationFn: deleteAdvisorHistory,
  });
}

export function useRefreshAdvisorHistory() {
  return useMutation({
    mutationFn: refreshAdvisorHistory,
  });
}

export function useAdvisorReminders(params?: { status?: string; category?: string; severity?: string; advisor_result_id?: number; page?: number; page_size?: number }) {
  return useQuery({
    queryKey: ['advisor-reminders', params],
    queryFn: () => fetchAdvisorReminders(params),
  });
}

export function useRefreshAdvisorReminders() {
  return useMutation({
    mutationFn: refreshAdvisorReminders,
  });
}

export function useUpdateAdvisorReminder() {
  return useMutation({
    mutationFn: updateAdvisorReminder,
  });
}

export function useAdvisorReminderPreference(profileKey?: string) {
  return useQuery({
    queryKey: ['advisor-reminder-preference', profileKey || 'default'],
    queryFn: () => fetchAdvisorReminderPreference(profileKey),
  });
}

export function useUpdateAdvisorReminderPreference() {
  return useMutation({
    mutationFn: updateAdvisorReminderPreference,
  });
}

export function useCreateAdvisorReminderDigest() {
  return useMutation({
    mutationFn: createAdvisorReminderDigest,
  });
}

export function useCreateAdvisorExecutionRecord() {
  return useMutation({
    mutationFn: createAdvisorExecutionRecord,
  });
}

export function useUpdateAdvisorExecutionRecord() {
  return useMutation({
    mutationFn: updateAdvisorExecutionRecord,
  });
}

export function useDeleteAdvisorExecutionRecord() {
  return useMutation({
    mutationFn: deleteAdvisorExecutionRecord,
  });
}

export function useImportAdvisorExecutionRecords() {
  return useMutation({
    mutationFn: importAdvisorExecutionRecords,
  });
}

export function useAdvisorPositions() {
  return useQuery({
    queryKey: ['advisor-positions'],
    queryFn: fetchAdvisorPositions,
    retry: false,
  });
}

export function useAdvisorPositionImportHistory(params?: { page?: number; page_size?: number; limit?: number }) {
  return useQuery({
    queryKey: ['advisor-position-import-history', params],
    queryFn: () => fetchAdvisorPositionImportHistory(params),
  });
}

export function useDownloadAdvisorPositionsTemplate() {
  return useMutation({
    mutationFn: downloadAdvisorPositionsTemplate,
  });
}

export function useRestoreAdvisorPositionsFromImportHistory() {
  return useMutation({
    mutationFn: restoreAdvisorPositionsFromImportHistory,
  });
}

export function useReplaceAdvisorPositions() {
  return useMutation({
    mutationFn: replaceAdvisorPositions,
  });
}

export function useImportAdvisorPositions() {
  return useMutation({
    mutationFn: importAdvisorPositions,
  });
}

export function useSnapshotVersions(params?: SnapshotVersionQueryParams) {
  return useQuery({
    queryKey: ['advisor-snapshot-versions', params],
    queryFn: () => fetchSnapshotVersions(params),
    enabled: Boolean(params?.provider && params?.fund_code && params?.endpoint),
  });
}

export function useDownloadSnapshotVersion() {
  return useMutation({
    mutationFn: downloadSnapshotVersion,
  });
}

// ---------------------------------------------------------------------------
// 建议引擎回测验证
// ---------------------------------------------------------------------------

export interface AdvisorBacktestRequest {
  fund_code: string;
  lookback_days?: number;
  rebalance_freq?: number;
  risk_level?: RiskLevel;
}

export interface AdvisorBacktestMetrics {
  total_advice_days: number;
  signals: { buy: number; sell: number; hold: number };
  hit_rates: {
    buy_5d: number | null;
    buy_10d: number | null;
    buy_20d: number | null;
    sell_5d: number | null;
    sell_10d: number | null;
    sell_20d: number | null;
  };
  avg_returns: {
    buy_5d: number | null;
    buy_10d: number | null;
    buy_20d: number | null;
    sell_5d: number | null;
    sell_10d: number | null;
    sell_20d: number | null;
  };
  simulated_portfolio: {
    total_return: number | null;
    annualized_return: number | null;
    max_drawdown: number | null;
    sharpe: number | null;
    benchmark_return: number | null;
  };
  signal_quality: {
    avg_confidence_correct: number | null;
    avg_confidence_wrong: number | null;
    information_coefficient: number | null;
  };
  fees: { total_paid: number; drag_pct: number };
}

export interface AdvisorBacktestResponse {
  fund_code: string;
  fund_name: string | null;
  start_date: string;
  end_date: string;
  config: Record<string, unknown>;
  metrics: AdvisorBacktestMetrics;
  equity_curve: Array<{ date: string; equity: number }>;
  advice_sample: Array<{
    date: string;
    action: string;
    score: number;
    confidence: number;
    return_5d: number | null;
    return_20d: number | null;
    hit_20d: boolean | null;
  }>;
  warnings: string[];
  disclaimer: string;
}

async function runAdvisorBacktest(payload: AdvisorBacktestRequest): Promise<AdvisorBacktestResponse> {
  const { data } = await apiClient.post<AdvisorBacktestResponse>('/v1/advisor/backtest', payload, {
    timeout: 120000, // 回测计算量大，超时设为 2 分钟
  });
  return data;
}

export function useAdvisorBacktest() {
  return useMutation({
    mutationFn: runAdvisorBacktest,
  });
}

// ---------------------------------------------------------------------------
// Walk-Forward 样本外验证
// ---------------------------------------------------------------------------

export interface WalkForwardRequest {
  fund_code: string;
  lookback_days?: number | null;
  n_folds?: number;
  rebalance_freq?: number;
  risk_level?: RiskLevel;
}

export interface WalkForwardFold {
  fold: number;
  train_period: string;
  test_period: string;
  in_sample_ic: number | null;
  oos_ic: number | null;
  oos_buy_hit_rate: number | null;
  oos_sell_hit_rate: number | null;
  oos_buy_count: number;
  oos_sell_count: number;
}

export interface AdvisorWalkForwardResponse {
  fund_code: string;
  fund_name: string | null;
  n_folds: number;
  train_window_days: number;
  test_window_days: number;
  data_info: {
    requested_days: number | null;
    actual_trading_days: number;
    data_start_date: string | null;
    data_end_date: string | null;
  };
  cpcv?: {
    pbo: number | null;
    avg_oos_sharpe: number | null;
    std_oos_sharpe: number | null;
    avg_is_sharpe: number | null;
    n_paths: number;
    n_splits: number;
    n_test_splits: number;
    is_overfit: boolean;
    warnings: string[];
  } | null;
  summary: {
    avg_oos_ic: number | null;
    avg_oos_buy_hit_rate: number | null;
    avg_oos_sell_hit_rate: number | null;
    avg_is_ic: number | null;
    ic_degradation: number | null;
    total_oos_signals: number;
    total_oos_buy: number;
    total_oos_sell: number;
    multi_objective_score?: number | null;
    multi_objective_components?: Record<string, number>;
    multi_objective_eliminated?: boolean;
    multi_objective_reasons?: string[];
    baseline_adjusted_score?: number | null;
    baseline_passed?: boolean | null;
    baseline_reasons?: string[];
  };
  multi_objective?: {
    score?: number | null;
    components?: Record<string, number>;
    eliminated?: boolean;
    reasons?: string[];
  } | null;
  baseline?: {
    adjusted_score?: number | null;
    passed?: boolean | null;
    reasons?: string[];
    best?: {
      name?: string;
      multi_objective_score?: number | null;
      sharpe?: number | null;
      total_return?: number | null;
      max_drawdown?: number | null;
      metrics?: Record<string, unknown>;
    } | null;
    comparison?: Record<string, {
      baseline_score?: number | null;
      score_uplift?: number | null;
      sharpe_uplift?: number | null;
      return_uplift?: number | null;
      drawdown_delta?: number | null;
      baseline_metrics?: Record<string, unknown>;
    }>;
    metrics?: Record<string, Record<string, unknown>>;
  } | null;
  baseline_adjusted_score?: number | null;
  baseline_passed?: boolean | null;
  baseline_reasons?: string[];
  baseline_comparison?: Record<string, {
    baseline_score?: number | null;
    score_uplift?: number | null;
    sharpe_uplift?: number | null;
    return_uplift?: number | null;
    drawdown_delta?: number | null;
    baseline_metrics?: Record<string, unknown>;
  }>;
  baseline_metrics?: Record<string, Record<string, unknown>>;
  folds: WalkForwardFold[];
  warnings: string[];
  disclaimer: string;
}

async function runWalkForward(payload: WalkForwardRequest): Promise<AdvisorWalkForwardResponse> {
  const { data } = await apiClient.post<AdvisorWalkForwardResponse>('/v1/advisor/walk-forward', payload, {
    timeout: 180000, // Walk-forward 计算量更大，3 分钟超时
  });
  return data;
}

export function useWalkForward() {
  return useMutation({
    mutationFn: runWalkForward,
  });
}

// ---------------------------------------------------------------------------
// 建议执行跟踪 + 引擎健康度
// ---------------------------------------------------------------------------

export interface TrackedReturnItem {
  action: string;
  composite_score: number;
  base_nav: number;
  evaluation_label?: 'effective' | 'neutral' | 'ineffective' | 'not_evaluable' | string;
  return_5d: number | null;
  return_10d: number | null;
  return_20d: number | null;
  return_60d: number | null;
  hit_5d: boolean | null;
  hit_10d: boolean | null;
  hit_20d: boolean | null;
  execution_attribution?: {
    latest_status: ExecutionStatus | 'no_record' | string;
    adopted: boolean;
    record_count: number;
    latest_executed_date?: string | null;
    total_executed_amount?: number | null;
    total_executed_shares?: number | null;
    amount_execution_ratio?: number | null;
    drift_level?: string | null;
  };
}

export interface AdvicePerformanceResponse {
  id: number;
  advice_date: string;
  status: 'pending' | 'tracked';
  tracked_at: string | null;
  tracked_returns: Record<string, TrackedReturnItem> | null;
  summary: {
    buy_hit_rate_20d: number | null;
    sell_hit_rate_20d: number | null;
    buy_avg_return_20d: number | null;
    sell_avg_return_20d: number | null;
    total_tracked: number;
    buy_count: number;
    sell_count: number;
    evaluation_labels?: {
      effective: number;
      neutral: number;
      ineffective: number;
      not_evaluable: number;
    };
    execution_summary?: AdvisorExecutionSummary;
  } | null;
  execution_summary?: AdvisorExecutionSummary;
  message?: string;
}

export interface EngineHealthResponse {
  status: 'healthy' | 'degraded' | 'unhealthy' | 'insufficient_data' | 'unknown';
  status_reason: string;
  rolling_ic: {
    ic_20d: number | null;
    samples: number;
    trend: 'improving' | 'stable' | 'declining' | 'critical';
    ic_3month_avg: number | null;
    ic_1month_avg: number | null;
  };
  hit_rates: {
    buy: number | null;
    sell: number | null;
    buy_count: number;
    sell_count: number;
  };
  last_validated: string | null;
  thresholds: {
    ic_healthy: number;
    ic_degraded: number;
    hit_rate_healthy: number;
    min_samples: number;
  };
  runtime_health?: {
    queue?: {
      status?: 'healthy' | 'degraded' | 'unavailable' | 'unknown' | string;
      redis_available?: boolean;
      broker_url_configured?: boolean;
      queues?: Record<string, number | null>;
      warnings?: string[];
      error?: string | null;
    };
  };
}

export interface OOSCoverageItem {
  exact_count: number;
  resolved_count: number;
  missing_count: number;
  fallback_to_moderate: number;
  fallback_to_latest: number;
  stale_count: number;
  exact_coverage_pct: number | null;
  resolved_coverage_pct: number | null;
}

export interface OOSStatusResponse {
  date: string;
  total_active_funds: number;
  latest_snapshot_update: string | null;
  nightly_refresh: {
    schedule: string;
    risk_level: string;
    lookback_days: number | null;
    n_folds: number;
    rebalance_freq: number;
    max_funds: number;
    max_age_days: number;
    dispatch_every_n: number;
    dispatch_countdown_step: number;
  };
  coverage: Record<RiskLevel, OOSCoverageItem>;
  fund_codes_sample: string[];
}

export interface TriggerOOSRefreshResponse {
  status: string;
  task_id: string;
  message: string;
  config: OOSStatusResponse['nightly_refresh'];
}

async function fetchAdvicePerformance(id: number): Promise<AdvicePerformanceResponse> {
  const { data } = await apiClient.get<AdvicePerformanceResponse>(`/v1/advisor/history/${id}/performance`);
  return data;
}

async function fetchEngineHealth(): Promise<EngineHealthResponse> {
  const { data } = await apiClient.get<EngineHealthResponse>('/v1/advisor/health');
  return data;
}

async function fetchOOSStatus(): Promise<OOSStatusResponse> {
  const { data } = await apiClient.get<OOSStatusResponse>('/v1/advisor/oos-status');
  return data;
}

async function triggerOOSRefresh(): Promise<TriggerOOSRefreshResponse> {
  const { data } = await apiClient.post<TriggerOOSRefreshResponse>('/v1/advisor/oos-refresh');
  return data;
}

export function useAdvicePerformance(id: number | null) {
  return useQuery({
    queryKey: ['advisor-performance', id],
    queryFn: () => fetchAdvicePerformance(id!),
    enabled: id != null,
  });
}

export function useEngineHealth() {
  return useQuery({
    queryKey: ['advisor-health'],
    queryFn: fetchEngineHealth,
    staleTime: 5 * 60 * 1000, // 5分钟缓存
  });
}

export function useOOSStatus() {
  return useQuery({
    queryKey: ['advisor-oos-status'],
    queryFn: fetchOOSStatus,
    staleTime: 5 * 60 * 1000,
  });
}

export function useTriggerOOSRefresh() {
  return useMutation({
    mutationFn: triggerOOSRefresh,
  });
}

// ---------------------------------------------------------------------------
// v4: 截面因子选基 API
// ---------------------------------------------------------------------------

export interface CrossSectionalRequest {
  fund_type?: string | null;
  min_history_days?: number;
  top_n?: number;
}

export interface CrossSectionalFundScore {
  fund_code: string;
  fund_name: string | null;
  composite_rank: number;
  factors: {
    alpha_persistence: number | null;
    sharpe_persistence: number | null;
    size_factor: number | null;
    fee_factor: number | null;
    drawdown_recovery: number | null;
    consistency: number | null;
  };
  ranks: {
    alpha: number | null;
    sharpe: number | null;
    size: number | null;
    fee: number | null;
    drawdown: number | null;
    consistency: number | null;
  };
}

export interface CrossSectionalResponse {
  eval_date: string;
  fund_type_filter: string | null;
  n_funds_evaluated: number;
  n_funds_qualified: number;
  top_funds: string[];
  bottom_funds: string[];
  factor_ics: Record<string, number | null>;
  avg_ic: number | null;
  fund_scores: CrossSectionalFundScore[];
  warnings: string[];
  methodology: string;
}

export interface CrossSectionalICResponse {
  fund_type: string | null;
  forward_days: number;
  n_funds: number | null;
  factor_ics: Record<string, number | null>;
  interpretation: string[];
  methodology: string;
}

async function runCrossSectionalScoring(payload: CrossSectionalRequest): Promise<CrossSectionalResponse> {
  const { data } = await apiClient.post<CrossSectionalResponse>('/v1/advisor/cross-sectional', payload);
  return data;
}

async function runCrossSectionalIC(payload: CrossSectionalRequest & { forward_days?: number }): Promise<CrossSectionalICResponse> {
  const { forward_days, ...body } = payload;
  const params = forward_days ? { forward_days } : {};
  const { data } = await apiClient.post<CrossSectionalICResponse>('/v1/advisor/cross-sectional/ic', body, { params });
  return data;
}

export function useCrossSectionalScoring() {
  return useMutation({
    mutationFn: runCrossSectionalScoring,
  });
}

export function useCrossSectionalIC() {
  return useMutation({
    mutationFn: runCrossSectionalIC,
  });
}
