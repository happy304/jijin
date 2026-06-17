import { useQuery } from '@tanstack/react-query';
import apiClient from './client';

export interface MetricDefinition {
  key: string;
  name: string;
  formula: string;
  annualization: string | null;
  sign: string;
  insufficient_data: string;
  usage: string;
}

export interface MetricDefinitionsResponse {
  metric_version: string;
  frequency: number;
  definitions: MetricDefinition[];
}

async function fetchMetricDefinitions(): Promise<MetricDefinitionsResponse> {
  const { data } = await apiClient.get<MetricDefinitionsResponse>('/v1/meta/metric-definitions');
  return data;
}

export function useMetricDefinitions() {
  return useQuery({
    queryKey: ['meta', 'metric-definitions'],
    queryFn: fetchMetricDefinitions,
    staleTime: 60 * 60 * 1000,
  });
}
