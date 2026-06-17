export const ADVISOR_POSITIONS_STORAGE_KEY = 'advisor_positions';

export interface AdvisorPositionItem {
  fund_code: string;
  market_value: number;
  shares: number;
  buy_date: string;
  cost_basis: number;
  // 兼容旧本地缓存字段
  amount?: number;
  cost?: number;
}

export function normalizeAdvisorPositionItem(item: {
  fund_code?: string;
  market_value?: number;
  shares?: number;
  cost_basis?: number;
  buy_date?: string | null;
  amount?: number;
  cost?: number;
}): AdvisorPositionItem {
  return {
    fund_code: String(item.fund_code || ''),
    market_value: Number(item.market_value ?? item.amount ?? 0),
    shares: Number(item.shares ?? item.amount ?? 0),
    buy_date: String(item.buy_date || ''),
    cost_basis: Number(item.cost_basis ?? item.cost ?? 0),
  };
}

export function loadSavedAdvisorPositions(): AdvisorPositionItem[] {
  try {
    const saved = localStorage.getItem(ADVISOR_POSITIONS_STORAGE_KEY);
    if (saved) {
      const parsed = JSON.parse(saved) as Array<Record<string, unknown>>;
      return parsed.map((item) => normalizeAdvisorPositionItem(item));
    }
  } catch {
    // ignore
  }
  return [];
}

export function saveAdvisorPositions(items: AdvisorPositionItem[]): void {
  localStorage.setItem(ADVISOR_POSITIONS_STORAGE_KEY, JSON.stringify(items));
}

export function toPersistedAdvisorPositionPayload(items: AdvisorPositionItem[]) {
  const deduped = new Map<string, AdvisorPositionItem>();
  items.forEach((item) => {
    const normalized = normalizeAdvisorPositionItem(item);
    if (!normalized.fund_code) return;
    deduped.set(normalized.fund_code, normalized);
  });
  return Array.from(deduped.values())
    .sort((a, b) => a.fund_code.localeCompare(b.fund_code))
    .map((item) => ({
      fund_code: item.fund_code,
      market_value: item.market_value || 0,
      shares: item.shares || 0,
      cost_basis: item.cost_basis || 0,
      buy_date: item.buy_date || null,
    }));
}

export function buildAdvisorPositionsMap(items: AdvisorPositionItem[]): Record<string, number> {
  const map: Record<string, number> = {};
  items.forEach((position) => {
    if (position.fund_code) {
      map[position.fund_code] = position.market_value;
    }
  });
  return map;
}

export function buildAdvisorPositionsDetail(items: AdvisorPositionItem[]): Record<string, { market_value?: number; shares?: number; cost_basis?: number; buy_date?: string; amount?: number; cost?: number }> {
  const map: Record<string, { market_value?: number; shares?: number; cost_basis?: number; buy_date?: string; amount?: number; cost?: number }> = {};
  items.forEach((position) => {
    if (position.fund_code) {
      map[position.fund_code] = {
        market_value: position.market_value > 0 ? position.market_value : undefined,
        shares: position.shares > 0 ? position.shares : undefined,
        cost_basis: position.cost_basis > 0 ? position.cost_basis : undefined,
        buy_date: position.buy_date || undefined,
        // 兼容旧接口/旧历史结构
        amount: position.shares > 0 ? position.shares : undefined,
        cost: position.cost_basis > 0 ? position.cost_basis : undefined,
      };
    }
  });
  return map;
}

export function createEmptyAdvisorPosition(): AdvisorPositionItem {
  return {
    fund_code: '',
    market_value: 0,
    shares: 0,
    buy_date: '',
    cost_basis: 0,
  };
}

export function appendEmptyAdvisorPosition(items: AdvisorPositionItem[]): AdvisorPositionItem[] {
  return [...items, createEmptyAdvisorPosition()];
}

export function removeAdvisorPositionAt(items: AdvisorPositionItem[], index: number): AdvisorPositionItem[] {
  return items.filter((_, itemIndex) => itemIndex !== index);
}

export function updateAdvisorPositionAt(
  items: AdvisorPositionItem[],
  index: number,
  field: keyof AdvisorPositionItem,
  value: string | number,
): AdvisorPositionItem[] {
  return items.map((item, itemIndex) => (
    itemIndex === index
      ? { ...item, [field]: value }
      : item
  ));
}
