import { useState } from 'react';
import { Alert, Button, Card, Col, Descriptions, Empty, Form, InputNumber, Row, Select, Space, Statistic, Table, Tag, Typography } from 'antd';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import {
  useAdvisorBacktest,
  type AdvisorBacktestResponse,
  type RiskLevel,
} from '@/api/advisor';
import type { FundOptionSummary } from '@/api/funds';
import { formatPct } from '@/utils/advisorDisplay';

const { Text } = Typography;

interface FundOption { value: string; label: string; }

const RISK_OPTIONS = [
  { value: 'conservative', label: '保守型 — 高门槛，低仓位' },
  { value: 'moderate', label: '稳健型 — 平衡配置' },
  { value: 'aggressive', label: '进取型 — 低门槛，高仓位' },
];

const ACTION_CONFIG = {
  buy: { text: '可关注增配', tagColor: 'red' },
  sell: { text: '可关注减配', tagColor: 'green' },
  hold: { text: '继续观察', tagColor: 'default' },
  watch: { text: '观察', tagColor: 'blue' },
};

export function AdvisorBacktestPanel({ fundOptions, fundMap }: { fundOptions: FundOption[]; fundMap: Map<string, FundOptionSummary> }) {
  const [form] = Form.useForm();
  const backtestMutation = useAdvisorBacktest();
  const [result, setResult] = useState<AdvisorBacktestResponse | null>(null);
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

  const handleRun = async (values: { fund_code: string; lookback_days: number; rebalance_freq: number; risk_level: string }) => {
    const res = await backtestMutation.mutateAsync({
      fund_code: values.fund_code,
      lookback_days: useAllData ? undefined : values.lookback_days,
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
        message="引擎历史验证"
        description="对组合检查引擎进行历史样本验证：在历史每个调仓日运行引擎，统计参考结果后的实际表现和关注命中率。注意：这是样本内/历史验证，仅用于观察模型稳定性，不能证明未来有效，也不构成交易建议。"
        style={{ marginBottom: 16 }}
      />

      <Card style={{ marginBottom: 16 }}>
        <Form form={form} layout="inline" onFinish={handleRun} initialValues={{ lookback_days: 750, rebalance_freq: 5, risk_level: 'moderate' }}>
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
                  <InputNumber min={300} step={100} placeholder="天数" />
                </Form.Item>
              )}
            </Space>
          </Form.Item>
          <Form.Item name="rebalance_freq" label="调仓频率(天)">
            <InputNumber min={1} max={20} />
          </Form.Item>
          <Form.Item name="risk_level" label="风险偏好">
            <Select options={RISK_OPTIONS} style={{ width: 160 }} />
          </Form.Item>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={backtestMutation.isPending}>运行验证</Button>
          </Form.Item>
        </Form>
        {selectedFundInfo?.inception_date && (
          <div style={{ marginTop: 8 }}>
            <Text type="secondary" style={{ fontSize: 12 }}>
              基金成立日期: {selectedFundInfo.inception_date}
              {useAllData && '（将使用上市以来全部数据，确保信号样本量充足）'}
            </Text>
          </div>
        )}
      </Card>

      {backtestMutation.isError && (
        <Alert type="error" message="验证失败" description={backtestMutation.error instanceof Error ? backtestMutation.error.message : '请检查参数'} showIcon closable style={{ marginBottom: 16 }} />
      )}

      {result && (
        <>
          {result.warnings.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message="验证警告"
              description={<ul style={{ margin: 0, paddingLeft: 16 }}>{result.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
              style={{ marginBottom: 16 }}
            />
          )}

          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={4}>
              <Card size="small">
                <Statistic title="IC (信息系数)" value={result.metrics.signal_quality.information_coefficient ?? '-'} precision={4} valueStyle={{ color: (result.metrics.signal_quality.information_coefficient ?? 0) > 0.05 ? '#3f8600' : '#cf1322' }} />
              </Card>
            </Col>
            <Col span={4}>
              <Card size="small">
                <Statistic title="增配关注命中率(20日)" value={result.metrics.hit_rates.buy_20d != null ? `${(result.metrics.hit_rates.buy_20d * 100).toFixed(1)}%` : '-'} />
              </Card>
            </Col>
            <Col span={4}>
              <Card size="small">
                <Statistic title="减配关注命中率(20日)" value={result.metrics.hit_rates.sell_20d != null ? `${(result.metrics.hit_rates.sell_20d * 100).toFixed(1)}%` : '-'} />
              </Card>
            </Col>
            <Col span={4}>
              <Card size="small">
                <Statistic title="模拟年化收益" value={result.metrics.simulated_portfolio.annualized_return != null ? `${(result.metrics.simulated_portfolio.annualized_return * 100).toFixed(2)}%` : '-'} valueStyle={{ color: (result.metrics.simulated_portfolio.annualized_return ?? 0) > 0 ? '#3f8600' : '#cf1322' }} />
              </Card>
            </Col>
            <Col span={4}>
              <Card size="small">
                <Statistic title="基准收益(持有)" value={result.metrics.simulated_portfolio.benchmark_return != null ? `${(result.metrics.simulated_portfolio.benchmark_return * 100).toFixed(2)}%` : '-'} />
              </Card>
            </Col>
            <Col span={4}>
              <Card size="small">
                <Statistic title="夏普比率" value={result.metrics.simulated_portfolio.sharpe ?? '-'} precision={2} />
              </Card>
            </Col>
          </Row>

          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={12}>
              <Card size="small" title="命中率统计">
                <Descriptions column={2} size="small">
                  <Descriptions.Item label="增配关注5日">{result.metrics.hit_rates.buy_5d != null ? `${(result.metrics.hit_rates.buy_5d * 100).toFixed(1)}%` : '-'}</Descriptions.Item>
                  <Descriptions.Item label="减配关注5日">{result.metrics.hit_rates.sell_5d != null ? `${(result.metrics.hit_rates.sell_5d * 100).toFixed(1)}%` : '-'}</Descriptions.Item>
                  <Descriptions.Item label="增配关注10日">{result.metrics.hit_rates.buy_10d != null ? `${(result.metrics.hit_rates.buy_10d * 100).toFixed(1)}%` : '-'}</Descriptions.Item>
                  <Descriptions.Item label="减配关注10日">{result.metrics.hit_rates.sell_10d != null ? `${(result.metrics.hit_rates.sell_10d * 100).toFixed(1)}%` : '-'}</Descriptions.Item>
                  <Descriptions.Item label="增配关注20日">{result.metrics.hit_rates.buy_20d != null ? `${(result.metrics.hit_rates.buy_20d * 100).toFixed(1)}%` : '-'}</Descriptions.Item>
                  <Descriptions.Item label="减配关注20日">{result.metrics.hit_rates.sell_20d != null ? `${(result.metrics.hit_rates.sell_20d * 100).toFixed(1)}%` : '-'}</Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>
            <Col span={12}>
              <Card size="small" title="信号统计">
                <Descriptions column={2} size="small">
                  <Descriptions.Item label="总分析天数">{result.metrics.total_advice_days}</Descriptions.Item>
                  <Descriptions.Item label="增配关注信号">{result.metrics.signals.buy}</Descriptions.Item>
                  <Descriptions.Item label="减配关注信号">{result.metrics.signals.sell}</Descriptions.Item>
                  <Descriptions.Item label="继续观察信号">{result.metrics.signals.hold}</Descriptions.Item>
                  <Descriptions.Item label="正确时置信度">{result.metrics.signal_quality.avg_confidence_correct?.toFixed(3) ?? '-'}</Descriptions.Item>
                  <Descriptions.Item label="错误时置信度">{result.metrics.signal_quality.avg_confidence_wrong?.toFixed(3) ?? '-'}</Descriptions.Item>
                </Descriptions>
              </Card>
            </Col>
          </Row>

          <Card size="small" title={`模拟组合 vs 基准 (${result.start_date} ~ ${result.end_date})`} style={{ marginBottom: 16 }}>
            <Row gutter={16}>
              <Col span={6}><Statistic title="模拟总收益" value={formatPct(result.metrics.simulated_portfolio.total_return)} valueStyle={{ color: (result.metrics.simulated_portfolio.total_return ?? 0) > 0 ? '#3f8600' : '#cf1322' }} /></Col>
              <Col span={6}><Statistic title="最大回撤" value={formatPct(result.metrics.simulated_portfolio.max_drawdown)} valueStyle={{ color: '#cf1322' }} /></Col>
              <Col span={6}><Statistic title="费用拖累" value={formatPct(result.metrics.fees.drag_pct)} /></Col>
              <Col span={6}><Statistic title="总费用" value={`¥${result.metrics.fees.total_paid.toFixed(0)}`} /></Col>
            </Row>

            {result.equity_curve && result.equity_curve.length > 1 && (
              <div style={{ marginTop: 16 }}>
                <ReactECharts
                  option={{
                    tooltip: { trigger: 'axis', formatter: (params: any) => {
                      const p = Array.isArray(params) ? params[0] : params;
                      return `${p.axisValue}<br/>权益: ¥${Number(p.value).toLocaleString()}`;
                    }},
                    grid: { left: 60, right: 20, top: 20, bottom: 40 },
                    xAxis: { type: 'category', data: result.equity_curve.map(p => p.date), axisLabel: { rotate: 30, fontSize: 10 } },
                    yAxis: { type: 'value', axisLabel: { formatter: (v: number) => `¥${(v/1000).toFixed(0)}k` } },
                    series: [{
                      type: 'line',
                      data: result.equity_curve.map(p => p.equity),
                      smooth: true,
                      lineStyle: { width: 2, color: '#1890ff' },
                      areaStyle: { color: 'rgba(24,144,255,0.1)' },
                      symbol: 'none',
                    }],
                  } as EChartsOption}
                  style={{ height: 240 }}
                />
              </div>
            )}
          </Card>

          {result.advice_sample.length > 0 && (
            <Card size="small" title={`检查结论样本（前${result.advice_sample.length}条）`}>
              <Table
                size="small"
                dataSource={result.advice_sample}
                rowKey="date"
                pagination={{ pageSize: 10 }}
                columns={[
                  { title: '日期', dataIndex: 'date', width: 100 },
                  { title: '检查结论', dataIndex: 'action', width: 60, render: (a: string) => { const c = ACTION_CONFIG[a as keyof typeof ACTION_CONFIG] || ACTION_CONFIG.hold; return <Tag color={c.tagColor}>{c.text}</Tag>; } },
                  { title: '评分', dataIndex: 'score', width: 70, render: (v: number) => <Text style={{ color: v > 0.3 ? '#cf1322' : v < -0.3 ? '#3f8600' : '#666' }}>{(v * 100).toFixed(0)}</Text> },
                  { title: '置信度', dataIndex: 'confidence', width: 70, render: (v: number) => `${(v * 100).toFixed(0)}%` },
                  { title: '5日收益', dataIndex: 'return_5d', width: 80, render: (v: number | null) => v != null ? `${(v * 100).toFixed(2)}%` : '-' },
                  { title: '20日收益', dataIndex: 'return_20d', width: 80, render: (v: number | null) => v != null ? `${(v * 100).toFixed(2)}%` : '-' },
                  { title: '20日命中', dataIndex: 'hit_20d', width: 70, render: (v: boolean | null) => v === true ? <Tag color="green">✓</Tag> : v === false ? <Tag color="red">✗</Tag> : '-' },
                ]}
              />
            </Card>
          )}

          <Alert type="warning" message={result.disclaimer} showIcon style={{ marginTop: 16 }} />
        </>
      )}

      {!result && !backtestMutation.isPending && (
        <Card>
          <Empty description="选择一只基金，运行引擎历史验证，查看信号有效性" />
          <div style={{ marginTop: 16, textAlign: 'center' }}>
            <Text type="secondary">
              验证方法：在历史每个调仓日运行引擎（只用当时可用数据），统计参考结论后的实际表现。
              IC &gt; 0.05 表示信号有一定预测力，命中率 &gt; 55% 表示方向判断优于随机。
            </Text>
          </div>
        </Card>
      )}
    </div>
  );
}
