import type {
  AdvisorHoldingImportResponse,
  AdvisorHoldingImportRowResult,
  AdvisorPositionImportRestoreResponse,
} from '@/api/advisor';
import { formatCurrency, hasImportGovernanceWarnings } from '@/utils/advisorDisplay';

export function getFailedAdvisorPositionImportRows(result: AdvisorHoldingImportResponse): AdvisorHoldingImportRowResult[] {
  return result.rows.filter((row) => row.status === 'failed');
}

export function shouldShowAdvisorPositionImportReview(result: AdvisorHoldingImportResponse): boolean {
  return getFailedAdvisorPositionImportRows(result).length > 0 || hasImportGovernanceWarnings(result.governance_summary);
}

export function buildAdvisorPositionImportModalTitle(result: AdvisorHoldingImportResponse): string {
  return `持仓导入完成：成功 ${result.imported_count} 条，失败 ${result.failed_count} 条`;
}

export function buildAdvisorPositionImportSuccessMessage(result: AdvisorHoldingImportResponse): string {
  return `成功导入 ${result.imported_count} 条持仓，并已保存到服务端；总市值 ${formatCurrency(result.governance_summary.total_market_value)}`;
}

export function buildAdvisorPositionRestoreSuccessMessage(restored: AdvisorPositionImportRestoreResponse): string {
  return `已从 ${restored.restored_from.filename} 恢复 ${restored.total} 条持仓快照`;
}
