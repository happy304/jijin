export const ADVISOR_VIEW_MODE_STORAGE_KEY = 'advisor_view_mode';
export const ADVISOR_RECENT_FUNDS_STORAGE_KEY = 'advisor_recent_funds';
export const ADVISOR_FAVORITE_GROUPS_STORAGE_KEY = 'advisor_favorite_groups';
export const ADVISOR_REMINDER_PREFS_STORAGE_KEY = 'advisor_reminder_prefs';

export type AdvisorViewMode = 'novice' | 'expert';
export type AdvisorFavoriteGroup = { name: string; fund_codes: string[] };
export type AdvisorReminderPreferenceCategory = 'validity' | 'risk' | 'execution' | 'plan';

export function normalizeAdvisorFundCodes(codes: string[]): string[] {
  return Array.from(new Set(codes.map((code) => String(code || '').trim()).filter(Boolean)));
}

export function mergeRecentAdvisorFundCodes(codes: string[], previous: string[]): string[] {
  const normalized = normalizeAdvisorFundCodes(codes);
  if (normalized.length === 0) return previous;
  return Array.from(new Set([...normalized, ...previous])).slice(0, 8);
}

export function buildAdvisorFavoriteGroup(
  fundCodes: string[],
  existingGroups: AdvisorFavoriteGroup[],
): AdvisorFavoriteGroup | null {
  const normalized = normalizeAdvisorFundCodes(fundCodes);
  if (normalized.length === 0) return null;
  return {
    name: `自选组合${existingGroups.length + 1}`,
    fund_codes: normalized,
  };
}

export function prependAdvisorFavoriteGroup(
  group: AdvisorFavoriteGroup,
  previous: AdvisorFavoriteGroup[],
): AdvisorFavoriteGroup[] {
  return [group, ...previous.filter((item) => item.name !== group.name)].slice(0, 6);
}

export function advisorViewModeLabel(mode: AdvisorViewMode): string {
  return mode === 'novice' ? '新手模式' : '专家模式';
}

export function loadAdvisorViewMode(): AdvisorViewMode {
  try {
    const saved = localStorage.getItem(ADVISOR_VIEW_MODE_STORAGE_KEY);
    if (saved === 'novice' || saved === 'expert') {
      return saved;
    }
  } catch {
    // ignore
  }
  return 'novice';
}

export function loadRecentFunds(): string[] {
  try {
    const saved = JSON.parse(localStorage.getItem(ADVISOR_RECENT_FUNDS_STORAGE_KEY) || '[]') as string[];
    return Array.isArray(saved) ? saved.filter(Boolean).slice(0, 8) : [];
  } catch {
    return [];
  }
}

export function loadFavoriteGroups(): AdvisorFavoriteGroup[] {
  try {
    const saved = JSON.parse(localStorage.getItem(ADVISOR_FAVORITE_GROUPS_STORAGE_KEY) || '[]') as AdvisorFavoriteGroup[];
    return Array.isArray(saved) ? saved.filter((item) => item && item.name && Array.isArray(item.fund_codes)) : [];
  } catch {
    return [];
  }
}

export function loadReminderPrefs(): AdvisorReminderPreferenceCategory[] {
  try {
    const saved = JSON.parse(localStorage.getItem(ADVISOR_REMINDER_PREFS_STORAGE_KEY) || '[]') as string[];
    const allowed: AdvisorReminderPreferenceCategory[] = ['validity', 'risk', 'execution', 'plan'];
    const normalized = Array.isArray(saved)
      ? saved.filter((item): item is AdvisorReminderPreferenceCategory => allowed.includes(item as AdvisorReminderPreferenceCategory))
      : [];
    return normalized.length > 0 ? normalized : allowed;
  } catch {
    return ['validity', 'risk', 'execution', 'plan'];
  }
}

export function saveAdvisorViewMode(mode: AdvisorViewMode): void {
  localStorage.setItem(ADVISOR_VIEW_MODE_STORAGE_KEY, mode);
}

export function saveRecentFunds(fundCodes: string[]): void {
  localStorage.setItem(ADVISOR_RECENT_FUNDS_STORAGE_KEY, JSON.stringify(fundCodes.slice(0, 8)));
}

export function saveFavoriteGroups(groups: AdvisorFavoriteGroup[]): void {
  localStorage.setItem(ADVISOR_FAVORITE_GROUPS_STORAGE_KEY, JSON.stringify(groups.slice(0, 6)));
}

export function saveReminderPrefs(categories: AdvisorReminderPreferenceCategory[]): void {
  localStorage.setItem(ADVISOR_REMINDER_PREFS_STORAGE_KEY, JSON.stringify(categories));
}
