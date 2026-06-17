import type {
  AdvisorAnalyzeResponse,
  AdvisorExecutionContextResponse,
  AdvisorExecutionPlanStatus,
  TradingAdviceItem,
} from '@/api/advisor';
import { adviceActionLabel, adviceStrengthLabel } from '@/utils/advisorDisplay';

export interface AdvisorAuditExportPayloadArgs {
  scope: 'current_result' | 'history_detail';
  viewMode: 'novice' | 'expert';
  adviceDate: string;
  riskLevel: string;
  totalCapital: number;
  fundCodes: string[];
  strategyName?: string | null;
  summary: AdvisorAnalyzeResponse['summary'];
  userProfile?: Record<string, unknown> | null;
  positionsDetail?: Record<string, unknown> | null;
  executionContext?: AdvisorExecutionContextResponse | null;
  executionPlanStatus?: AdvisorExecutionPlanStatus | null;
  advices: TradingAdviceItem[];
  sourceId?: number | null;
}

export function buildAdvisorAuditExportPayload(args: AdvisorAuditExportPayloadArgs) {
  return {
    export_type: 'advisor_audit',
    scope: args.scope,
    exported_at: new Date().toISOString(),
    view_mode: args.viewMode,
    advice_date: args.adviceDate,
    risk_level: args.riskLevel,
    total_capital: args.totalCapital,
    fund_codes: args.fundCodes,
    strategy_name: args.strategyName || null,
    source_id: args.sourceId ?? null,
    summary: args.summary,
    user_profile: args.userProfile || null,
    positions_detail: args.positionsDetail || null,
    execution_context: args.executionContext || null,
    execution_plan_status: args.executionPlanStatus || null,
    advices: args.advices.map((advice) => ({
      fund_code: advice.fund_code,
      fund_name: advice.fund_name,
      action: advice.action,
      action_label: adviceActionLabel(advice.action),
      confidence: advice.confidence,
      strength_label: adviceStrengthLabel(advice.action, advice.confidence, advice.strength),
      suggested_amount: advice.suggested_amount,
      suggested_pct: advice.suggested_pct,
      position_after: advice.position_after,
      reasoning: advice.reasoning || null,
      reasons: advice.reasons || [],
      risk_warnings: advice.risk_warnings || [],
      trade_plan: advice.trade_plan || null,
      data_quality: advice.data_quality || null,
      overfit_risk: advice.overfit_risk || null,
      decision_audit: advice.decision_audit || null,
      reliability_adjustment: advice.reliability_adjustment || null,
      validity: advice.validity || null,
      trade_timing: advice.trade_timing || null,
      portfolio_impact: advice.portfolio_impact || null,
      suitability: advice.suitability || null,
      profile_constraints: advice.profile_constraints || [],
      fee_estimate: advice.fee_estimate || null,
      execution_plan_tasks: args.executionPlanStatus?.by_fund?.[advice.fund_code]?.tasks || null,
    })),
  };
}
