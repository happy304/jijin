export const FUND_TYPE_OPTIONS = [
  { label: '全部', value: '' },
  { label: '股票型', value: 'stock' },
  { label: '债券型', value: 'bond' },
  { label: '混合型', value: 'mixed' },
  { label: '货币型', value: 'money' },
  { label: 'QDII', value: 'qdii' },
  { label: 'FOF', value: 'fof' },
  { label: '指数型', value: 'index' },
];

export const FUND_TYPE_LABELS: Record<string, string> = {
  stock: '股票型',
  bond: '债券型',
  mixed: '混合型',
  money: '货币型',
  qdii: 'QDII',
  fof: 'FOF',
  index: '指数型',
};

export const FUND_TYPE_COLORS: Record<string, string> = {
  stock: 'red',
  bond: 'blue',
  mixed: 'purple',
  money: 'green',
  qdii: 'orange',
  fof: 'cyan',
  index: 'gold',
};

export function fundTypeLabel(type: string | null | undefined): string {
  if (!type) return '-';
  return FUND_TYPE_LABELS[type] || type;
}

export function fundTypeColor(type: string | null | undefined): string {
  if (!type) return 'default';
  return FUND_TYPE_COLORS[type] || 'default';
}
