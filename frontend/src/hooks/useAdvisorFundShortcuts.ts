import { useCallback } from 'react';
import { message } from 'antd';
import type { FormInstance } from 'antd';
import {
  buildAdvisorFavoriteGroup,
  mergeRecentAdvisorFundCodes,
  prependAdvisorFavoriteGroup,
  type AdvisorFavoriteGroup,
} from '@/utils/advisorPreferences';

export function useAdvisorFundShortcuts({
  activeTab,
  manualForm,
  selectedStrategyFundCodes,
  favoriteGroups,
  setActiveTab,
  setRecentFunds,
  setFavoriteGroups,
}: {
  activeTab: string;
  manualForm: FormInstance;
  selectedStrategyFundCodes?: string[];
  favoriteGroups: AdvisorFavoriteGroup[];
  setActiveTab: (tab: string) => void;
  setRecentFunds: React.Dispatch<React.SetStateAction<string[]>>;
  setFavoriteGroups: React.Dispatch<React.SetStateAction<AdvisorFavoriteGroup[]>>;
}) {
  const appendManualFundCode = useCallback((code: string) => {
    const current = (manualForm.getFieldValue('fund_codes') as string[] | undefined) || [];
    manualForm.setFieldValue('fund_codes', Array.from(new Set([...current, code])));
  }, [manualForm]);

  const rememberFundCodes = useCallback((codes: string[]) => {
    setRecentFunds((prev) => mergeRecentAdvisorFundCodes(codes, prev));
  }, [setRecentFunds]);

  const saveCurrentSelectionAsFavorite = useCallback(() => {
    const fundCodes = activeTab === 'manual'
      ? ((manualForm.getFieldValue('fund_codes') as string[] | undefined) || [])
      : (selectedStrategyFundCodes || []);
    const group = buildAdvisorFavoriteGroup(fundCodes, favoriteGroups);
    if (!group) {
      message.warning('请先选择基金后再保存为自选组合');
      return;
    }
    setFavoriteGroups((prev) => prependAdvisorFavoriteGroup(group, prev));
    message.success(`已保存为${group.name}`);
  }, [activeTab, favoriteGroups, manualForm, selectedStrategyFundCodes, setFavoriteGroups]);

  const applyFavoriteGroup = useCallback((fundCodes: string[]) => {
    setActiveTab('manual');
    manualForm.setFieldValue('fund_codes', fundCodes);
    rememberFundCodes(fundCodes);
  }, [manualForm, rememberFundCodes, setActiveTab]);

  return {
    appendManualFundCode,
    rememberFundCodes,
    saveCurrentSelectionAsFavorite,
    applyFavoriteGroup,
  };
}
