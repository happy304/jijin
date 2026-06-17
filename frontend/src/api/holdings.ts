/**
 * Holdings analysis API hooks.
 *
 * Provides hooks for:
 * - Portfolio penetration (underlying stock exposure)
 * - Holdings similarity between funds
 * - Find funds by stock (reverse lookup)
 */

import { useQuery, useMutation } from '@tanstack/react-query';
import apiClient from './client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface StockExposureItem {
  stock_code: string;
  stock_name: string | null;
  weight: number;
  funds: string[];
  industry: string | null;
}

export interface IndustryItem {
  industry: string;
  weight: number;
  stock_count: number;
}

export interface PenetrateResponse {
  stock_exposures: StockExposureItem[];
  industry_distribution: IndustryItem[];
  top5_concentration: number;
  top10_concentration: number;
  hhi: number;
  total_stocks: number;
}

export interface SimilarityItem {
  fund_a: string;
  fund_b: string;
  cosine_similarity: number;
  overlap_count: number;
  overlap_stocks: string[];
}

export interface SimilarityResponse {
  pairs: SimilarityItem[];
  avg_similarity: number;
  max_similarity: number;
  warning: string | null;
}

export interface StockFundItem {
  fund_code: string;
  fund_name: string | null;
  weight: number;
  report_date: string;
}

export interface StockFundResponse {
  stock_code: string;
  stock_name: string | null;
  funds: StockFundItem[];
  total: number;
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

async function penetrateHoldings(
  fundCodes: string[],
  fundWeights?: Record<string, number>,
): Promise<PenetrateResponse> {
  const { data } = await apiClient.post<PenetrateResponse>('/v1/holdings/penetrate', {
    fund_codes: fundCodes,
    fund_weights: fundWeights,
  });
  return data;
}

async function computeSimilarity(fundCodes: string[]): Promise<SimilarityResponse> {
  const { data } = await apiClient.post<SimilarityResponse>('/v1/holdings/similarity', {
    fund_codes: fundCodes,
  });
  return data;
}

async function findFundsByStock(stockCode: string, minWeight?: number): Promise<StockFundResponse> {
  const { data } = await apiClient.get<StockFundResponse>('/v1/holdings/by-stock', {
    params: { stock_code: stockCode, min_weight: minWeight || 0.01 },
  });
  return data;
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

export function usePenetrateHoldings() {
  return useMutation({
    mutationFn: ({ fundCodes, fundWeights }: { fundCodes: string[]; fundWeights?: Record<string, number> }) =>
      penetrateHoldings(fundCodes, fundWeights),
  });
}

export function useHoldingsSimilarity() {
  return useMutation({
    mutationFn: (fundCodes: string[]) => computeSimilarity(fundCodes),
  });
}

export function useFundsByStock(stockCode: string, minWeight?: number) {
  return useQuery({
    queryKey: ['holdings', 'by-stock', stockCode, minWeight],
    queryFn: () => findFundsByStock(stockCode, minWeight),
    enabled: stockCode.length >= 4,
  });
}
