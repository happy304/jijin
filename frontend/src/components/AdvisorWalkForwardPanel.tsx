import { useState } from 'react';
import { Alert, Button, Card, Col, Empty, Form, InputNumber, Row, Select, Space, Statistic, Table, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import { useWalkForward, type AdvisorWalkForwardResponse, type RiskLevel } from '@/api/advisor';
import type { FundOptionSummary } from '@/api/funds';
import { baselineNameLabel, formatPct } from '@/utils/advisorDisplay';

const { Text } = Typography;

const RISK_OPTIONS = [
  { value: 'conservative', label: '保守型 — 高门槛，低仓位' },
  { value: 'moderate', label: '稳健型 — 平衡配置' },
  { value: 'aggressive', label: '进取型 — 低门槛，高仓位' },
];

interface FundOption { value: string; label: string; }

interface AdvisorWalkForwardPanelProps {
  fundOptions: FundOption[];
  fundMap: Map<string, FundOptionSummary>;
}

export function AdvisorWalkForwardPanel({ fundOptions, fundMap }: AdvisorWalkForwardPanelProps) {
  const [form] = Form.useForm();
  const walkForwardMutation = useWalkForward();
  const [result, setResult] = useState<AdvisorWalkForwardResponse | null>(null);
  const [selectedFundInfo, setSelectedFundInfo] = useState<{ inception_date?: string } | null>(null);
  const [useAllData, setUseAllData] = useState(true);

  const handleFundChange = (code: string) => {
    const fund = fundMap.get(code);
    if (fund) {
      setSelectedFundInfo({ inception_date: fund.inception_date || undefined });
    } else {
      setSelectedFundInfo(null);
    }
  };

  const handleRun = async (values: { fund_code: string; lookback_days: number; n_folds: number; rebalance_freq: number; risk_level: string }) => {
    const res = await walkForwardMutation.mutateAsync({
      fund_code: values.fund_code,
      lookback_days: useAllData ? null : values.lookback_days,
      n_folds: values.n_folds,
      rebalance_freq: values.rebalance_freq,
      risk_level: values.risk_level as RiskLevel,
    });
    setResult(res);
  };

  return (
    <div>
      <Alert
        type="info"
        showIcon
        message="Walk-Forward 样本外验证"
        description="将数据严格分为训练期和测试期，测试期指标为真正的样本外表现。IC 衰减率反映过拟合程度：> 0.7 泛化良好，< 0.5 严重过拟合。系统会在每晚自动刷新样本外缓存；若当前风险档暂无精确缓存，会优先复用 moderate 或最近一次可用缓存。"
        style={{ marginBottom: 16 }}
      />

      <Card style={{ marginBottom: 16 }}>
        <Form form={form} layout="inline" onFinish={handleRun} initialValues={{ lookback_days: 750, n_folds: 5, rebalance_freq: 5, risk_level: 'moderate' }}>
          <Form.Item name="fund_code" label="选择基金" rules={[{ required: true, message: '请选择基金' }]}>
            <Select placeholder="选择一只基金" options={fundOptions} showSearch filterOption={(input, opt) => (opt?.label ?? '').toLowerCase().includes(input.toLowerCase())} style={{ width: 240 }} onChange={handleFundChange} />
          </Form.Item>
          <Form.Item label="数据范围">
            <Space>
              <Select
                value={useAllData ? 'all' : 'custom'}
                onChange={(v) => setUseAllData(v === 'all')}
                style={{ width: 120 }}
                options={[
                  { value: 'all', label: '全部数据' },
                  { value: 'custom', label: '自定义天数' },
                ]}
              />
              {!useAllData && (
                <Form.Item name="lookback_days" noStyle>
                  <InputNumber min={400} step={100} placeholder="天数" />
                </Form.Item>
              )}
            </Space>
          </Form.Item>
          <Form.Item name="n_folds" label="折叠数">
            <InputNumber min={3} max={10} />
          </Form.Item>
          <Form.Item name="rebalance_freq" label="调仓频率(天)">
            <InputNumber min={1} max={20} />
          </Form.Item>
          <Form.Item name="risk_level" label="风险偏好">
            <Select options={RISK_OPTIONS} style={{ width: 160 }} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={walkForwardMutation.isPending}>运行 OOS 验证</Button>
          </Form.Item>
        </Form>
        {selectedFundInfo?.inception_date && (
          <div style={{ marginTop: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              📅 基金成立日期: {selectedFundInfo.inception_date}
              {useAllData && '（将使用上市以来全部数据）'}
            </Text>
          </div>
        )}
      </Card>

      {walkForwardMutation.isError && (
        <Alert type="error" message="验证失败" description={walkForwardMutation.error instanceof Error ? walkForwardMutation.error.message : '请检查参数'} showIcon closable style={{ marginBottom: 16 }} />
      )}

      {result && (
        <>
          {result.warnings.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message="验证结论"
              description={<ul style={{ margin: 0, paddingLeft: 16 }}>{result.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
              style={{ marginBottom: 16 }}
            />
          )}

          {result.data_info && (
            <Alert
              type="info"
              showIcon
              message={`实际使用 ${result.data_info.actual_trading_days} 个交易日数据（${result.data_info.data_start_date} ~ ${result.data_info.data_end_date}）`}
              description={result.data_info.requested_days ? `请求 ${result.data_info.requested_days} 天` : '使用全部可用数据'}
              style={{ marginBottom: 16 }}
            />
          )}

          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={4}>
              <Card size="small">
                <Statistic title="样本内 IC" value={result.summary.avg_is_ic ?? '-'} precision={4} valueStyle={{ color: '#666' }} />
              </Card>
            </Col>
            <Col span={4}>
              <Card size="small">
                <Statistic title="样本外 IC" value={result.summary.avg_oos_ic ?? '-'} precision={4} valueStyle={{ color: (result.summary.avg_oos_ic ?? 0) > 0.05 ? '#3f8600' : (result.summary.avg_oos_ic ?? 0) > 0.02 ? '#d4b106' : '#cf1322' }} />
              </Card>
            </Col>
            <Col span={4}>
              <Card size="small">
                <Statistic
                  title="IC 衰减率"
                  value={result.summary.ic_degradation != null ? `${(result.summary.ic_degradation * 100).toFixed(0)}%` : '-'}
                  valueStyle={{ color: (result.summary.ic_degradation ?? 0) > 0.7 ? '#3f8600' : (result.summary.ic_degradation ?? 0) > 0.5 ? '#d4b106' : '#cf1322' }}
                />
              </Card>
            </Col>
            <Col span={4}>
              <Card size="small">
                <Statistic title="OOS 增配关注命中率" value={result.summary.avg_oos_buy_hit_rate != null ? `${(result.summary.avg_oos_buy_hit_rate * 100).toFixed(1)}%` : '-'} />
              </Card>
            </Col>
            <Col span={4}>
              <Card size="small">
                <Statistic title="OOS 减配关注命中率" value={result.summary.avg_oos_sell_hit_rate != null ? `${(result.summary.avg_oos_sell_hit_rate * 100).toFixed(1)}%` : '-'} />
              </Card>
            </Col>
            <Col span={4}>
              <Card size="small">
                <Statistic title="OOS 信号总数" value={result.summary.total_oos_signals} suffix={`(买${result.summary.total_oos_buy}/卖${result.summary.total_oos_sell})`} />
              </Card>
            </Col>
          </Row>

          {(result.multi_objective || result.baseline) && (
            <Card size="small" title="稳健性评分与 baseline 对照" style={{ marginBottom: 16 }}>
              <Row gutter={16} style={{ marginBottom: 12 }}>
                <Col span={6}>
                  <Statistic
                    title="多目标分"
                    value={result.multi_objective?.score ?? result.summary.multi_objective_score ?? '-'}
                    precision={3}
                    valueStyle={{ color: (result.multi_objective?.eliminated ?? result.summary.multi_objective_eliminated) ? '#cf1322' : '#3f8600' }}
                  />
                </Col>
                <Col span={6}>
                  <Statistic
                    title="baseline 调整后"
                    value={result.baseline?.adjusted_score ?? result.summary.baseline_adjusted_score ?? '-'}
                    precision={3}
                    valueStyle={{ color: (result.baseline?.passed ?? result.summary.baseline_passed) === false ? '#cf1322' : '#3f8600' }}
                  />
                </Col>
                <Col span={6}>
                  <Statistic
                    title="baseline 门槛"
                    value={(result.baseline?.passed ?? result.summary.baseline_passed) == null ? '-' : ((result.baseline?.passed ?? result.summary.baseline_passed) ? '通过' : '未通过')}
                    valueStyle={{ color: (result.baseline?.passed ?? result.summary.baseline_passed) ? '#3f8600' : '#cf1322', fontSize: 18 }}
                  />
                </Col>
                <Col span={6}>
                  <Statistic
                    title="最佳 baseline"
                    value={baselineNameLabel(result.baseline?.best?.name)}
                    valueStyle={{ fontSize: 18 }}
                  />
                </Col>
              </Row>

              {((result.multi_objective?.reasons?.length || 0) > 0 || (result.baseline?.reasons?.length || result.summary.baseline_reasons?.length || 0) > 0) && (
                <Alert
                  type={(result.baseline?.passed ?? result.summary.baseline_passed) === false || (result.multi_objective?.eliminated ?? result.summary.multi_objective_eliminated) ? 'warning' : 'info'}
                  showIcon
                  message="评分解释"
                  description={[
                    ...(result.multi_objective?.reasons || result.summary.multi_objective_reasons || []),
                    ...(result.baseline?.reasons || result.summary.baseline_reasons || []),
                  ].join('；')}
                  style={{ marginBottom: 12 }}
                />
              )}

              {!!result.baseline?.comparison && Object.keys(result.baseline.comparison).length > 0 && (
                <Table
                  size="small"
                  pagination={false}
                  rowKey={(row) => row.name}
                  dataSource={Object.entries(result.baseline.comparison).map(([name, item]) => ({ name, ...item }))}
                  columns={[
                    { title: 'Baseline', dataIndex: 'name', width: 120, render: (value: string) => baselineNameLabel(value) },
                    { title: 'Baseline 分数', dataIndex: 'baseline_score', width: 110, render: (v: number | null | undefined) => v != null ? v.toFixed(3) : '-' },
                    { title: '分数提升', dataIndex: 'score_uplift', width: 100, render: (v: number | null | undefined) => v != null ? <Text style={{ color: v >= 0 ? '#3f8600' : '#cf1322' }}>{v.toFixed(3)}</Text> : '-' },
                    { title: 'Sharpe 提升', dataIndex: 'sharpe_uplift', width: 110, render: (v: number | null | undefined) => v != null ? <Text style={{ color: v >= 0 ? '#3f8600' : '#cf1322' }}>{v.toFixed(3)}</Text> : '-' },
                    { title: '收益提升', dataIndex: 'return_uplift', width: 110, render: (v: number | null | undefined) => v != null ? <Text style={{ color: v >= 0 ? '#3f8600' : '#cf1322' }}>{formatPct(v)}</Text> : '-' },
                    { title: '回撤变化', dataIndex: 'drawdown_delta', width: 110, render: (v: number | null | undefined) => v != null ? <Text style={{ color: v <= 0 ? '#3f8600' : '#cf1322' }}>{formatPct(v)}</Text> : '-' },
                  ]}
                />
              )}
            </Card>
          )}

          {result.cpcv && (
            <Card size="small" title="CPCV / PBO 过拟合诊断" style={{ marginBottom: 16 }}>
              <Row gutter={16} style={{ marginBottom: 8 }}>
                <Col span={6}>
                  <Statistic title="PBO" value={result.cpcv.pbo != null ? `${(result.cpcv.pbo * 100).toFixed(0)}%` : '-'} valueStyle={{ color: (result.cpcv.pbo ?? 0) >= 0.5 ? '#cf1322' : '#3f8600' }} />
                </Col>
                <Col span={6}><Statistic title="CPCV路径" value={result.cpcv.n_paths} /></Col>
                <Col span={6}><Statistic title="平均OOS Sharpe" value={result.cpcv.avg_oos_sharpe ?? '-'} precision={3} /></Col>
                <Col span={6}><Statistic title="平均IS Sharpe" value={result.cpcv.avg_is_sharpe ?? '-'} precision={3} /></Col>
              </Row>
              {(result.cpcv.warnings || []).length > 0 && <Alert type="warning" showIcon message={result.cpcv.warnings.join('；')} />}
            </Card>
          )}

          {result.folds.length > 0 && (
            <Card size="small" title="各折叠 IC 对比（样本内 vs 样本外）" style={{ marginBottom: 16 }}>
              <ReactECharts
                option={{
                  tooltip: { trigger: 'axis' },
                  legend: { data: ['样本内 IC', '样本外 IC'], top: 0 },
                  grid: { left: 50, right: 20, top: 40, bottom: 30 },
                  xAxis: { type: 'category', data: result.folds.map(f => `Fold ${f.fold}`) },
                  yAxis: { type: 'value', name: 'IC', axisLabel: { formatter: (v: number) => v.toFixed(3) } },
                  series: [
                    { name: '样本内 IC', type: 'bar', data: result.folds.map(f => f.in_sample_ic), itemStyle: { color: '#91caff' } },
                    { name: '样本外 IC', type: 'bar', data: result.folds.map(f => f.oos_ic), itemStyle: { color: '#1890ff' } },
                  ],
                } as EChartsOption}
                style={{ height: 200 }}
              />
            </Card>
          )}

          <Card size="small" title="各折叠详情">
            <Table
              size="small"
              dataSource={result.folds}
              rowKey="fold"
              pagination={false}
              columns={[
                { title: '折叠', dataIndex: 'fold', width: 60 },
                { title: '训练期', dataIndex: 'train_period', width: 180 },
                { title: '测试期', dataIndex: 'test_period', width: 180 },
                { title: 'IS IC', dataIndex: 'in_sample_ic', width: 80, render: (v: number | null) => v != null ? v.toFixed(4) : '-' },
                { title: 'OOS IC', dataIndex: 'oos_ic', width: 80, render: (v: number | null) => <Text style={{ color: (v ?? 0) > 0.05 ? '#3f8600' : (v ?? 0) > 0 ? '#d4b106' : '#cf1322' }}>{v != null ? v.toFixed(4) : '-'}</Text> },
                { title: 'OOS 增配关注命中', dataIndex: 'oos_buy_hit_rate', width: 100, render: (v: number | null, r: { oos_buy_count: number; oos_sell_count: number }) => v != null ? `${(v * 100).toFixed(0)}% (${r.oos_buy_count})` : '-' },
                { title: 'OOS 减配关注命中', dataIndex: 'oos_sell_hit_rate', width: 100, render: (v: number | null, r: { oos_buy_count: number; oos_sell_count: number }) => v != null ? `${(v * 100).toFixed(0)}% (${r.oos_sell_count})` : '-' },
              ]}
            />
          </Card>

          <Alert type="warning" message={result.disclaimer} showIcon style={{ marginTop: 16 }} />
        </>
      )}

      {!result && !walkForwardMutation.isPending && (
        <Card>
          <Empty description="选择基金运行 Walk-Forward 验证，检测引擎是否存在过拟合" />
          <div style={{ marginTop: 16, textAlign: 'center' }}>
            <Text type="secondary">
              Walk-Forward 将数据分为训练期和测试期，测试期指标为真正的样本外表现。
              IC 衰减率 = OOS IC / IS IC，&gt; 70% 表示泛化良好，&lt; 50% 表示过拟合。
            </Text>
          </div>
        </Card>
      )}
    </div>
  );
}
