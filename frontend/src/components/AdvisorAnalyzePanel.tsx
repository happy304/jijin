import { Alert, Card, Form, Select, Tabs } from 'antd';
import type { FormInstance } from 'antd';
import { AdvisorCapitalRiskSubmitRow } from '@/components/AdvisorCapitalRiskSubmitRow';
import { AdvisorFundSelectionShortcuts } from '@/components/AdvisorFundSelectionShortcuts';
import { AdvisorInvestmentProfileFields } from '@/components/AdvisorInvestmentProfileFields';
import { AdvisorPositionImportHistoryCard } from '@/components/AdvisorPositionImportHistoryCard';
import { AdvisorPositionsEditor } from '@/components/AdvisorPositionsEditor';
import { AdvisorPositionsImportControls } from '@/components/AdvisorPositionsImportControls';
import type { AdvisorPositionImportHistoryResponse } from '@/api/advisor';
import type { AdvisorFavoriteGroup } from '@/utils/advisorPreferences';
import type { AdvisorFundOption } from '@/utils/advisorFundOptions';
import type { AdvisorAnalyzeFormValues, AdvisorStrategyAnalyzeFormValues } from '@/utils/advisorRequestPayloads';
import type { AdvisorPositionItem } from '@/utils/advisorPositions';

export function AdvisorAnalyzePanel({
  activeTab,
  manualForm,
  strategyForm,
  loading,
  recentFunds,
  favoriteGroups,
  hotFundCodes,
  fundOptions,
  strategyOptions,
  positions,
  selectedFundCodes,
  selectedStrategyFundCodes,
  importHistoryData,
  importHistoryLoading,
  syncingPositions,
  downloadingTemplate,
  importingPositions,
  restoringImportHistory,
  restoringImportId,
  onChangeTab,
  onManualAnalyze,
  onStrategyAnalyze,
  onPickFund,
  onApplyFavoriteGroup,
  onSaveCurrentSelection,
  onDownloadTemplate,
  onImportPositions,
  onImportHistoryPageChange,
  onRestorePositions,
  onAddPosition,
  onRemovePosition,
  onUpdatePosition,
}: {
  activeTab: string;
  manualForm: FormInstance;
  strategyForm: FormInstance;
  loading: boolean;
  recentFunds: string[];
  favoriteGroups: AdvisorFavoriteGroup[];
  hotFundCodes: string[];
  fundOptions: AdvisorFundOption[];
  strategyOptions: Array<{ value: number; label: string }>;
  positions: AdvisorPositionItem[];
  selectedFundCodes?: string[];
  selectedStrategyFundCodes?: string[];
  importHistoryData?: AdvisorPositionImportHistoryResponse;
  importHistoryLoading: boolean;
  syncingPositions: boolean;
  downloadingTemplate: boolean;
  importingPositions: boolean;
  restoringImportHistory: boolean;
  restoringImportId: number | null;
  onChangeTab: (tab: string) => void;
  onManualAnalyze: (values: AdvisorAnalyzeFormValues) => void;
  onStrategyAnalyze: (values: AdvisorStrategyAnalyzeFormValues) => void;
  onPickFund: (code: string) => void;
  onApplyFavoriteGroup: (fundCodes: string[]) => void;
  onSaveCurrentSelection: () => void;
  onDownloadTemplate: (format: 'csv' | 'xlsx') => void;
  onImportPositions: (file: File) => Promise<boolean>;
  onImportHistoryPageChange: (page: number) => void;
  onRestorePositions: (importId: number) => void;
  onAddPosition: () => void;
  onRemovePosition: (index: number) => void;
  onUpdatePosition: (index: number, field: keyof AdvisorPositionItem, value: string | number) => void;
}) {
  return (
    <Card style={{ marginBottom: 16 }}>
      <Tabs activeKey={activeTab} onChange={onChangeTab} items={[
        { key: 'manual', label: '手动选择基金', children: (
          <Form form={manualForm} layout="vertical" onFinish={onManualAnalyze} initialValues={{ total_capital: 100000, risk_level: 'moderate', compare_risk_levels: true }}>
            <Alert type="info" showIcon style={{ marginBottom: 12 }} message="我的投资情况" description="可直接选择基金生成组合检查；如果你是第一次建仓，也可以不填持仓，只填写总资金、风险偏好和月度预算。" />
            <AdvisorFundSelectionShortcuts
              recentFunds={recentFunds}
              favoriteGroups={favoriteGroups}
              hotFundCodes={hotFundCodes}
              onPickFund={onPickFund}
              onApplyFavoriteGroup={onApplyFavoriteGroup}
              onSaveCurrentSelection={onSaveCurrentSelection}
            />
            <Form.Item name="fund_codes" label="选择基金" rules={[{ required: true, message: '请选择至少一只基金' }]}>
              <Select mode="multiple" placeholder="搜索并选择基金（最多20只）" options={fundOptions} maxCount={20} showSearch filterOption={(input, opt) => (opt?.label ?? '').toLowerCase().includes(input.toLowerCase())} />
            </Form.Item>
            <AdvisorCapitalRiskSubmitRow loading={loading} submitText="生成组合检查" />
            <AdvisorInvestmentProfileFields />
          </Form>
        )},
        { key: 'strategy', label: '基于已有策略', children: (
          <Form form={strategyForm} layout="vertical" onFinish={onStrategyAnalyze} initialValues={{ total_capital: 100000, risk_level: 'moderate', compare_risk_levels: true }}>
            <Form.Item name="strategy_id" label="选择策略（自动使用策略的基金池和信号）" rules={[{ required: true, message: '请选择策略' }]}>
              <Select placeholder="选择已创建的策略" options={strategyOptions} showSearch filterOption={(input, opt) => (opt?.label ?? '').toLowerCase().includes(input.toLowerCase())} />
            </Form.Item>
            <AdvisorCapitalRiskSubmitRow loading={loading} submitText="基于策略生成组合检查" />
            <AdvisorInvestmentProfileFields />
          </Form>
        )},
      ]} />

      <AdvisorPositionsImportControls
        syncing={syncingPositions}
        downloading={downloadingTemplate}
        importing={importingPositions}
        onDownloadTemplate={onDownloadTemplate}
        onImportPositions={onImportPositions}
      />
      <AdvisorPositionImportHistoryCard
        data={importHistoryData}
        loading={importHistoryLoading}
        restoring={restoringImportHistory}
        restoringImportId={restoringImportId}
        onPageChange={onImportHistoryPageChange}
        onRestore={onRestorePositions}
      />
      <AdvisorPositionsEditor
        positions={positions}
        activeTab={activeTab}
        selectedFundCodes={selectedFundCodes}
        selectedStrategyFundCodes={selectedStrategyFundCodes}
        fundOptions={fundOptions}
        onAddPosition={onAddPosition}
        onRemovePosition={onRemovePosition}
        onUpdatePosition={onUpdatePosition}
      />
    </Card>
  );
}
