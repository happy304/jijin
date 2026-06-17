/**
 * Fund API hooks using TanStack Query.
 *
 * Provides hooks for:
 * - Fund list search with pagination and filters
 * - Fund detail by code
 * - Fund NAV time series
 */

import { useQuery } from '@tanstack/react-query';
import apiClient from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface FundSummary {
  code: string;
  name: string;
  fund_type: string | null;
  status: string;
  inception_date: string | null;
  management_fee: string | null;
  company_id: string | null;
}

export interface PaginatedFunds {
  items: FundSummary[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface FundDetail {
  code: string;
  name: string;
  fund_type: string | null;
  sub_type: string | null;
  company_id: string | null;
  inception_date: string | null;
  benchmark: string | null;
  management_fee: string | null;
  custodian_fee: string | null;
  currency: string;
  status: string;
  is_purchasable: boolean;
  purchase_limit: string | null;
  source: string | null;
}

export interface NavItem {
  trade_date: string;
  unit_nav: string | null;
  accum_nav: string | null;
  adj_nav: string | null;
  daily_return: string | null;
}

export interface NavResponse {
  fund_code: string;
  start_date: string;
  end_date: string;
  count: number;
  needs_ingest: boolean;
  records: NavItem[];
}

export interface FundListParams {
  page?: number;
  page_size?: number;
  fund_type?: string;
  keyword?: string;
  company_id?: string;
  status?: string;
}

export interface NavQueryParams {
  start_date?: string;
  end_date?: string;
}

export interface NavQualityIssue {
  issue_type: string;
  start_date: string | null;
  end_date: string | null;
  trade_date: string | null;
  severity: 'info' | 'warning' | 'poor' | string;
  message: string;
}

export interface NavQualityResponse {
  fund_code: string;
  fund_type: string | null;
  start_date: string;
  end_date: string;
  total_calendar_days: number;
  total_nav_points: number;
  first_nav_date: string | null;
  last_nav_date: string | null;
  coverage_ratio: number;
  adj_nav_points: number;
  unit_nav_fallback_points: number;
  adj_nav_coverage_ratio: number;
  max_gap_days: number;
  spike_threshold: string;
  spike_count: number;
  status: 'good' | 'warning' | 'poor' | string;
  issues: NavQualityIssue[];
}

export interface NavQualityOverviewItem extends NavQualityResponse {
  fund_name: string;
}

export interface NavQualityOverviewResponse {
  items: NavQualityOverviewItem[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  status_counts: Record<string, number>;
}

export interface NavQualityOverviewParams extends NavQueryParams {
  page?: number;
  page_size?: number;
  fund_type?: string;
  status?: string;
  keyword?: string;
}

export interface FundOptionSummary {
  code: string;
  name: string;
  fund_type: string | null;
  status: string;
  inception_date: string | null;
}

export interface FundOptionsResponse {
  items: FundOptionSummary[];
}

export interface OnlineSearchResult {
  code: string;
  name: string;
  fund_type: string | null;
  in_database: boolean;
  nav_status: 'none' | 'partial' | 'full';
}

export interface OnlineSearchResponse {
  results: OnlineSearchResult[];
  source: string;
}

export interface IngestResponse {
  status: string;
  fund_code: string;
  fund_name: string | null;
  message: string;
  task_id: string | null;
}

export interface IngestTaskStatus {
  task_id: string;
  state: 'PENDING' | 'STARTED' | 'SUCCESS' | 'FAILURE' | 'RETRY';
  progress: string | null;
  result: Record<string, unknown> | null;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

async function fetchFunds(params: FundListParams): Promise<PaginatedFunds> {
  const { data } = await apiClient.get<PaginatedFunds>('/v1/funds', { params });
  return data;
}

async function fetchFundOptions(): Promise<FundOptionsResponse> {
  const { data } = await apiClient.get<FundOptionsResponse>('/v1/funds/options');
  return data;
}

async function fetchFundDetail(code: string): Promise<FundDetail> {
  const { data } = await apiClient.get<FundDetail>(`/v1/funds/${code}`);
  return data;
}

async function fetchFundNav(code: string, params?: NavQueryParams): Promise<NavResponse> {
  const { data } = await apiClient.get<NavResponse>(`/v1/funds/${code}/nav`, { params });
  return data;
}

async function fetchFundNavQuality(code: string, params?: NavQueryParams): Promise<NavQualityResponse> {
  const { data } = await apiClient.get<NavQualityResponse>(`/v1/funds/${code}/nav-quality`, { params });
  return data;
}

async function fetchFundNavQualityOverview(params: NavQualityOverviewParams): Promise<NavQualityOverviewResponse> {
  const { data } = await apiClient.get<NavQualityOverviewResponse>('/v1/funds/nav-quality-overview', { params });
  return data;
}

// ---------------------------------------------------------------------------
// Query hooks
// ---------------------------------------------------------------------------

export function useFundList(params: FundListParams) {
  return useQuery({
    queryKey: ['funds', params],
    queryFn: () => fetchFunds(params),
    placeholderData: (previousData) => previousData,
  });
}

export function useFundOptions() {
  return useQuery({
    queryKey: ['funds', 'options'],
    queryFn: fetchFundOptions,
    staleTime: 5 * 60 * 1000,
  });
}

export function useFundDetail(code: string) {
  return useQuery({
    queryKey: ['fund', code],
    queryFn: () => fetchFundDetail(code),
    enabled: !!code,
  });
}

export function useFundNav(code: string, params?: NavQueryParams) {
  return useQuery({
    queryKey: ['fundNav', code, params],
    queryFn: () => fetchFundNav(code, params),
    enabled: !!code,
  });
}

export function useFundNavQuality(code: string, params?: NavQueryParams) {
  return useQuery({
    queryKey: ['fundNavQuality', code, params],
    queryFn: () => fetchFundNavQuality(code, params),
    enabled: !!code,
  });
}

export function useFundNavQualityOverview(params: NavQualityOverviewParams) {
  return useQuery({
    queryKey: ['fundNavQualityOverview', params],
    queryFn: () => fetchFundNavQualityOverview(params),
    placeholderData: (previousData) => previousData,
  });
}


// ---------------------------------------------------------------------------
// Online search & ingest API functions
// ---------------------------------------------------------------------------

async function onlineSearchFunds(keyword: string): Promise<OnlineSearchResponse> {
  const { data } = await apiClient.get<OnlineSearchResponse>('/v1/funds/online-search', {
    params: { keyword },
  });
  return data;
}

async function ingestFund(code: string): Promise<IngestResponse> {
  const { data } = await apiClient.post<IngestResponse>(`/v1/funds/ingest/${code}`);
  return data;
}

async function fetchIngestStatus(taskId: string): Promise<IngestTaskStatus> {
  const { data } = await apiClient.get<IngestTaskStatus>(`/v1/funds/ingest-status/${taskId}`);
  return data;
}

export interface DeleteFundResponse {
  status: string;
  fund_code: string;
  message: string;
}

async function deleteFund(code: string): Promise<DeleteFundResponse> {
  const { data } = await apiClient.delete<DeleteFundResponse>(`/v1/funds/${code}`);
  return data;
}

// ---------------------------------------------------------------------------
// Online search & ingest hooks
// ---------------------------------------------------------------------------

export function useOnlineSearch(keyword: string) {
  // 纯数字必须是完整6位代码才触发；中文名称至少2个字符
  const isDigitOnly = /^\d+$/.test(keyword);
  const shouldFetch = isDigitOnly ? keyword.length === 6 : keyword.length >= 2;

  return useQuery({
    queryKey: ['onlineSearch', keyword],
    queryFn: () => onlineSearchFunds(keyword),
    enabled: shouldFetch,
    staleTime: 30000,
  });
}

export { ingestFund, fetchIngestStatus, deleteFund };

// ---------------------------------------------------------------------------
// 持仓分布
// ---------------------------------------------------------------------------

export interface HoldingPositionItem {
  stock_code: string;
  stock_name: string | null;
  weight: number;
  shares: number | null;
  market_value: number | null;
  industry: string | null;
}

export interface IndustryDistItem {
  industry: string;
  weight: number;
  stock_count: number;
}

export interface FundHoldingsResponse {
  fund_code: string;
  report_date: string | null;
  positions: HoldingPositionItem[];
  industry_distribution: IndustryDistItem[];
  top5_concentration: number;
  top10_concentration: number;
  total_stocks: number;
}

async function fetchFundHoldings(code: string): Promise<FundHoldingsResponse> {
  const { data } = await apiClient.get<FundHoldingsResponse>(`/v1/funds/${code}/holdings`);
  return data;
}

export function useFundHoldings(code: string) {
  return useQuery({
    queryKey: ['fundHoldings', code],
    queryFn: () => fetchFundHoldings(code),
    enabled: !!code,
    staleTime: 60000, // 持仓数据变化不频繁，缓存1分钟
  });
}

// ---------------------------------------------------------------------------
// 估值分析
// ---------------------------------------------------------------------------

export interface ValuationItem {
  fund_code: string;
  current_nav: number;
  percentile: number;
  zone: 'low' | 'normal' | 'high';
  suggestion: string;
  history_days: number;
  history_low: number;
  history_high: number;
  history_median: number;
}

export interface ValuationResponse {
  funds: ValuationItem[];
}

async function fetchValuation(fundCodes: string[], lookbackDays?: number): Promise<ValuationResponse> {
  const { data } = await apiClient.post<ValuationResponse>('/v1/funds/valuation', {
    fund_codes: fundCodes,
    lookback_days: lookbackDays || 750,
  });
  return data;
}

export function useValuation(fundCodes: string[], lookbackDays?: number) {
  return useQuery({
    queryKey: ['valuation', fundCodes, lookbackDays],
    queryFn: () => fetchValuation(fundCodes, lookbackDays),
    enabled: fundCodes.length > 0,
  });
}
