import { Alert, Card, Collapse, Descriptions, Space, Table, Tag, Typography } from 'antd';
import type { AdvisorExecutionContextResponse } from '@/api/advisor';
import { AdvisorSnapshotVersionLookupPanel as SnapshotVersionLookupPanel } from '@/components/AdvisorSnapshotVersionLookupPanel';
import {
  compactHash,
  formatAuditValue,
  formatMaybePct,
  oosSelectionSourceLabel,
} from '@/utils/advisorDisplay';

const { Text } = Typography;
const { Panel } = Collapse;

type AdvisorViewMode = 'novice' | 'expert';

export function AdvisorExecutionAuditCard({
  context,
  viewMode,
}: {
  context: AdvisorExecutionContextResponse;
  viewMode: AdvisorViewMode;
}) {
  const sources = context.data_sources || {};
  const navRows = Object.entries(sources.nav_by_fund || {}).map(([fund_code, info]) => ({ fund_code, ...info }));
  const signalRows = Object.entries(sources.signals_by_fund || {}).map(([fund_code, info]) => ({ fund_code, ...info }));
  const oosRows = Object.entries(sources.oos_by_fund || {}).map(([fund_code, info]) => ({ fund_code, ...info }));
  const rulesRows = Object.entries(sources.rules_by_fund || {}).map(([fund_code, info]) => ({ fund_code, ...info }));
  const macro = sources.macro_cutoff || {};

  return (
    <Card size="small" title="历史执行审计" style={{ marginBottom: 16 }}>
      <Descriptions column={3} size="small" style={{ marginBottom: 8 }}>
        <Descriptions.Item label="执行模式">{formatAuditValue(context.analysis_mode)}</Descriptions.Item>
        <Descriptions.Item label="分析时点">{formatAuditValue(context.requested_as_of_date)}</Descriptions.Item>
        <Descriptions.Item label="风险档">{formatAuditValue(context.resolved_risk_level)}</Descriptions.Item>
        <Descriptions.Item label="宏观分">{formatAuditValue(context.macro_score)}</Descriptions.Item>
        <Descriptions.Item label="宏观状态">{formatAuditValue(macro.market_state)}</Descriptions.Item>
        <Descriptions.Item label="估值状态">{formatAuditValue(macro.valuation_state)}</Descriptions.Item>
      </Descriptions>
      {viewMode === 'novice' && (
        <Alert
          type="info"
          showIcon
          message="新手模式下默认把审计明细折叠显示"
          description="重点先看分析时点、风险档和数据质量提示；如需逐数据源排查，可展开下方各审计分组。"
          style={{ marginBottom: 8 }}
        />
      )}
      {(context.data_quality_warnings || []).length > 0 && (
        <Alert
          type="warning"
          showIcon
          message="数据质量提示"
          description={(context.data_quality_warnings || []).slice(0, 6).join('；')}
          style={{ marginBottom: 8 }}
        />
      )}
      <Collapse ghost>
        <Panel header={`NAV 数据源（${navRows.length}）`} key="nav">
          <Table
            size="small"
            pagination={false}
            rowKey="fund_code"
            dataSource={navRows}
            scroll={{ x: 1200 }}
            columns={[
              { title: '基金', dataIndex: 'fund_code', width: 100 },
              { title: '样本数', dataIndex: 'point_count', width: 90, render: (v: unknown) => formatAuditValue(v) },
              { title: '起始', dataIndex: 'min_date', width: 120, render: (v: unknown) => formatAuditValue(v) },
              { title: '截止', dataIndex: 'max_date', width: 120, render: (v: unknown) => formatAuditValue(v) },
              { title: '状态', dataIndex: 'has_data', width: 90, render: (v: unknown) => <Tag color={v ? 'green' : 'red'}>{v ? '有数据' : '缺失'}</Tag> },
              { title: '快照版本', dataIndex: 'snapshot_version_id', width: 150, render: (v: unknown) => <Text code>{String(v || '-')}</Text> },
              { title: '捕获时间', dataIndex: 'snapshot_captured_at', width: 180, render: (v: unknown) => formatAuditValue(v) },
              { title: 'Hash', dataIndex: 'snapshot_sha256', width: 130, render: (v: unknown) => <Text code>{compactHash(String(v || ''))}</Text> },
            ]}
            expandable={{
              expandedRowRender: (row: any) => (
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Space wrap>
                    {row.snapshot_provider ? <Tag color="purple">主来源：{String(row.snapshot_provider)}</Tag> : null}
                    {row.snapshot_lookup_as_of ? <Tag>按时点回放：{String(row.snapshot_lookup_as_of)}</Tag> : null}
                  </Space>
                  <SnapshotVersionLookupPanel provider={String(row.snapshot_provider || '')} fundCode={String(row.fund_code)} endpoint="nav_history" asOf={String(context.requested_as_of_date || '')} />
                </Space>
              ),
              rowExpandable: (row) => Boolean(row.snapshot_provider),
            }}
          />
        </Panel>
        <Panel header={`策略信号源（${signalRows.length}）`} key="signals">
          <Table
            size="small"
            pagination={false}
            rowKey="fund_code"
            dataSource={signalRows}
            columns={[
              { title: '基金', dataIndex: 'fund_code', width: 100 },
              { title: '日期', dataIndex: 'signal_date', width: 120, render: (v: unknown) => formatAuditValue(v) },
              { title: '方向', dataIndex: 'direction', width: 90, render: (v: unknown) => formatAuditValue(v) },
              { title: '强度', dataIndex: 'strength', width: 90, render: (v: unknown) => formatAuditValue(v) },
              { title: '目标权重', dataIndex: 'target_weight', width: 100, render: (v: unknown) => formatMaybePct(v) },
              { title: '状态', dataIndex: 'has_signal', width: 90, render: (v: unknown) => <Tag color={v ? 'blue' : 'default'}>{v ? '命中' : '无信号'}</Tag> },
            ]}
          />
        </Panel>
        <Panel header={`OOS/PBO 快照（${oosRows.length}）`} key="oos">
          <Table
            size="small"
            pagination={false}
            rowKey="fund_code"
            dataSource={oosRows}
            scroll={{ x: 1200 }}
            columns={[
              { title: '基金', dataIndex: 'fund_code', width: 100 },
              { title: '风险档', dataIndex: 'risk_level', width: 90, render: (v: unknown) => formatAuditValue(v) },
              { title: '来源', dataIndex: 'selection_source', width: 120, render: (v: unknown) => oosSelectionSourceLabel(String(v || '')) },
              { title: '更新日', dataIndex: 'updated_at', width: 110, render: (v: unknown) => formatAuditValue(v) },
              { title: '快照日', dataIndex: 'snapshot_date', width: 110, render: (v: unknown) => formatAuditValue(v) },
              { title: '数据版本', dataIndex: 'data_version', width: 130, render: (v: unknown) => formatAuditValue(v) },
              { title: '配置哈希', dataIndex: 'config_hash', width: 130, render: (v: unknown) => <Text code>{compactHash(String(v || ''))}</Text> },
              { title: 'OOS IC', dataIndex: 'avg_oos_ic', width: 90, render: (v: unknown) => formatAuditValue(v) },
              { title: 'IC衰减', dataIndex: 'ic_degradation', width: 90, render: (v: unknown) => formatAuditValue(v) },
              { title: 'PBO', dataIndex: 'pbo', width: 90, render: (v: unknown) => formatMaybePct(v) },
              { title: 'CPCV路径', dataIndex: 'cpcv_n_paths', width: 90, render: (v: unknown) => formatAuditValue(v) },
              { title: '信号数', dataIndex: 'total_oos_signals', width: 90, render: (v: unknown) => formatAuditValue(v) },
            ]}
          />
        </Panel>
        <Panel header={`申赎规则快照（${rulesRows.length}）`} key="rules">
          <Table
            size="small"
            pagination={false}
            rowKey="fund_code"
            dataSource={rulesRows}
            scroll={{ x: 1200 }}
            columns={[
              { title: '基金', dataIndex: 'fund_code', width: 100 },
              { title: '状态', dataIndex: 'status', width: 90, render: (v: unknown) => formatAuditValue(v) },
              { title: '可申购', dataIndex: 'is_purchasable', width: 90, render: (v: unknown) => formatAuditValue(v) },
              { title: '可赎回', dataIndex: 'is_redeemable', width: 90, render: (v: unknown) => formatAuditValue(v) },
              { title: '申购限额', dataIndex: 'purchase_limit', width: 100, render: (v: unknown) => formatAuditValue(v) },
              { title: '最低申购', dataIndex: 'min_purchase_amount', width: 100, render: (v: unknown) => formatAuditValue(v) },
              { title: '阶段', dataIndex: 'fund_phase', width: 90, render: (v: unknown) => formatAuditValue(v) },
              { title: '退市日', dataIndex: 'delisting_date', width: 120, render: (v: unknown) => formatAuditValue(v) },
              { title: '快照版本', dataIndex: 'snapshot_version_id', width: 150, render: (v: unknown) => <Text code>{String(v || '-')}</Text> },
              { title: '捕获时间', dataIndex: 'snapshot_captured_at', width: 180, render: (v: unknown) => formatAuditValue(v) },
              { title: 'Hash', dataIndex: 'snapshot_sha256', width: 130, render: (v: unknown) => <Text code>{compactHash(String(v || ''))}</Text> },
            ]}
            expandable={{
              expandedRowRender: (row: any) => (
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Space wrap>
                    {row.source ? <Tag color="purple">主来源：{String(row.source)}</Tag> : null}
                    {row.source_updated_at ? <Tag>来源更新时间：{String(row.source_updated_at)}</Tag> : null}
                    {row.snapshot_lookup_as_of ? <Tag>按时点回放：{String(row.snapshot_lookup_as_of)}</Tag> : null}
                  </Space>
                  <SnapshotVersionLookupPanel provider={String(row.source || '')} fundCode={String(row.fund_code)} endpoint="fund_meta" asOf={String(context.requested_as_of_date || '')} />
                </Space>
              ),
              rowExpandable: (row) => Boolean(row.source),
            }}
          />
        </Panel>
      </Collapse>
    </Card>
  );
}
