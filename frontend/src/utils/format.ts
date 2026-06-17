/**
 * Format a number with specified decimal places.
 */
export function formatNumber(value: number | null | undefined, decimals = 2): string {
  if (value == null || isNaN(value)) return '--';
  return value.toFixed(decimals);
}

/**
 * Format a number as percentage (e.g., 0.1234 → "12.34%").
 */
export function formatPercent(value: number | null | undefined, decimals = 2): string {
  if (value == null || isNaN(value)) return '--';
  return `${(value * 100).toFixed(decimals)}%`;
}

/**
 * Format a number as CNY currency.
 */
export function formatCurrency(value: number | null | undefined, decimals = 2): string {
  if (value == null || isNaN(value)) return '--';
  return `¥${value.toLocaleString('zh-CN', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`;
}
