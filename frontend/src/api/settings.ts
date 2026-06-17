import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import apiClient from './client';

export interface ScheduleTaskProfile {
  name: string;
  task: string;
  queue: string | null;
  enabled: boolean;
}

export interface FeatureProfileResponse {
  personal_mode: boolean;
  feature_ai: boolean;
  feature_advisor_governance: boolean;
  feature_full_monitoring: boolean;
  schedule_mode: 'light' | 'research' | 'full' | string;
  schedule_enabled_tasks: ScheduleTaskProfile[];
  schedule_disabled_tasks: ScheduleTaskProfile[];
}

export interface FeatureProfileUpdateRequest {
  personal_mode?: boolean;
  feature_ai?: boolean;
  feature_advisor_governance?: boolean;
  feature_full_monitoring?: boolean;
  schedule_mode?: 'light' | 'research' | 'full';
}

async function fetchFeatureProfile(): Promise<FeatureProfileResponse> {
  const { data } = await apiClient.get<FeatureProfileResponse>('/v1/settings/features');
  return data;
}

async function updateFeatureProfile(payload: FeatureProfileUpdateRequest): Promise<FeatureProfileResponse> {
  const { data } = await apiClient.post<FeatureProfileResponse>('/v1/settings/features', payload);
  return data;
}

export function useFeatureProfile() {
  return useQuery({
    queryKey: ['settings', 'features'],
    queryFn: fetchFeatureProfile,
    staleTime: 5 * 60 * 1000,
    retry: 1,
  });
}

export function useUpdateFeatureProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updateFeatureProfile,
    onSuccess: (data) => {
      queryClient.setQueryData(['settings', 'features'], data);
    },
  });
}
