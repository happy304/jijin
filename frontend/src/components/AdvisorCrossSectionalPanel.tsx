import { useState } from 'react';
import { Alert, Button, Card, Col, Empty, InputNumber, Progress, Row, Select, Space, Statistic, Table, Tag, Typography } from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';
import {
  useCrossSectionalIC,
  useCrossSectionalScoring,
  type CrossSectionalICResponse,
  type CrossSectionalResponse,
} from '@/api/advisor';

const { Text } = Typography;

const FUND_TYPE_OPTIONS = [
  { value: 'stock', label: '股票型' },
  { value: 'mixed', label: '混合型' },
  { value: 'index', label: '指数型' },
  { value: 'bond', label: '债券型' },
  { value: 'qdii', label: 'QDII' },
  { value: 'fof', label: 'FOF' },
];

export function AdvisorCrossSectionalPanel() {
  const [fundType, setFundType] = useState<string>('stock');
  const [topN, setTopN] = useState<number>(10);
  const [csResult, setCsResult] = useState<CrossSectionalResponse | null>(null);
  const [icResult, setIcResult] = useState<CrossSectionalICResponse | null>(null);

  const scoringMutation = useCrossSectionalScoring();
  const icMutation = useCrossSectionalIC();

  const handleRunScoring = async () => {
    const res = await scoringMutation.mutateAsync({
      fund_type: fundType,
      min_history_days: 252,
      top_n: topN,
    });
    setCsResult(res);
  };

  const handleRunIC = async () => {
    const res = await icMutation.mutateAsync({
      fund_type: fundType,
      min_history_days: 252,
      forward_days: 20,
    });
    setIcResult(res);
  };

  return (
    <div>
      <Card title="截面因子选基" style={{ marginBottom: 16 }}>
        <Alert
          type="info"
          showIcon
          message="截面选基：在同类基金中按多因子综合排名，选出相对最优的基金"
          description="不预测绝对涨跌，只预测相对优劣。因子包括：Alpha持续性、Sharpe持续性、费率、回撤恢复、收益一致性。"
          style={{ marginBottom: 16 }}
        />

        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={6}>
            <Text strong>基金类型</Text>
            <Select
              value={fundType}
              onChange={setFundType}
              options={FUND_TYPE_OPTIONS}
              style={{ width: '100%', marginTop: 4 }}
            />
          </Col>
          <Col span={4}>
            <Text strong>Top N</Text>
            <InputNumber
              value={topN}
              onChange={(v) => setTopN(v || 10)}
              min={5}
              max={30}
              style={{ width: '100%', marginTop: 4 }}
            />
          </Col>
          <Col span={6} style={{ display: 'flex', alignItems: 'flex-end' }}>
            <Space>
              <Button
                type="primary"
                onClick={handleRunScoring}
                loading={scoringMutation.isPending}
                icon={<ThunderboltOutlined />}
              >
                运行截面排名
              </Button>
              <Button
                onClick={handleRunIC}
                loading={icMutation.isPending}
              >
                IC 验证
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      {icResult && (
        <Card title="因子 IC 验证（多期滚动）" size="small" style={{ marginBottom: 16 }}>
          <Row gutter={16} style={{ marginBottom: 12 }}>
            <Col span={6}><Statistic title="基金数" value={icResult.n_funds ?? '-'} /></Col>
            <Col span={6}><Statistic title="前瞻天数" value={icResult.forward_days} suffix="天" /></Col>
          </Row>
          <Table
            size="small"
            dataSource={icResult.interpretation.map((text, i) => ({ key: i, text }))}
            columns={[{ title: '因子 IC 解读', dataIndex: 'text', render: (t: string) => {
              const color = t.includes('✓') ? '#3f8600' : t.includes('✗') ? '#cf1322' : '#666';
              return <Text style={{ color }}>{t}</Text>;
            }}]}
            pagination={false}
          />
          <div style={{ marginTop: 12 }}>
            <Text type="secondary">{icResult.methodology}</Text>
          </div>
        </Card>
      )}

      {csResult && (
        <Card title={`截面排名结果 — ${csResult.fund_type_filter || '全部'}型 (${csResult.n_funds_qualified}只)`} size="small">
          {csResult.warnings.length > 0 && (
            <Alert type="warning" message={csResult.warnings.join('；')} showIcon style={{ marginBottom: 12 }} />
          )}

          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={6}><Statistic title="参与评分" value={csResult.n_funds_qualified} suffix="只" /></Col>
            <Col span={6}><Statistic title="评估日期" value={csResult.eval_date} /></Col>
            <Col span={12}>
              <Text strong>Top 基金：</Text>
              <Space wrap style={{ marginTop: 4 }}>
                {csResult.top_funds.map(code => <Tag key={code} color="red">{code}</Tag>)}
              </Space>
            </Col>
          </Row>

          <Table
            size="small"
            dataSource={csResult.fund_scores}
            rowKey="fund_code"
            pagination={{ pageSize: 15 }}
            columns={[
              { title: '排名', key: 'rank', width: 60, render: (_, __, i) => i + 1 },
              { title: '基金代码', dataIndex: 'fund_code', width: 90 },
              { title: '基金名称', dataIndex: 'fund_name', width: 180, ellipsis: true },
              { title: '综合排名', dataIndex: 'composite_rank', width: 90, render: (v: number) => (
                <Progress
                  percent={Math.round(v * 100)}
                  size="small"
                  status={v > 0.8 ? 'success' : v < 0.2 ? 'exception' : 'normal'}
                  format={(p) => `${p}%`}
                />
              )},
              { title: 'Alpha', key: 'alpha', width: 70, render: (_, r) => r.ranks.alpha != null ? `${(r.ranks.alpha * 100).toFixed(0)}%` : '-' },
              { title: 'Sharpe', key: 'sharpe', width: 70, render: (_, r) => r.ranks.sharpe != null ? `${(r.ranks.sharpe * 100).toFixed(0)}%` : '-' },
              { title: '费率', key: 'fee', width: 70, render: (_, r) => r.ranks.fee != null ? `${(r.ranks.fee * 100).toFixed(0)}%` : '-' },
              { title: '回撤恢复', key: 'dd', width: 80, render: (_, r) => r.ranks.drawdown != null ? `${(r.ranks.drawdown * 100).toFixed(0)}%` : '-' },
              { title: '一致性', key: 'consist', width: 70, render: (_, r) => r.ranks.consistency != null ? `${(r.ranks.consistency * 100).toFixed(0)}%` : '-' },
            ]}
          />

          <div style={{ marginTop: 12 }}>
            <Text type="secondary">{csResult.methodology}</Text>
          </div>
        </Card>
      )}

      {!csResult && !icResult && !scoringMutation.isPending && (
        <Card>
          <Empty description="选择基金类型，点击「运行截面排名」查看同类基金的因子排序" />
          <div style={{ marginTop: 16, textAlign: 'center' }}>
            <Text type="secondary">
              截面排名百分位：100% = 同类最优，0% = 同类最差。Top 20% 可作为进一步复核候选。
            </Text>
          </div>
        </Card>
      )}
    </div>
  );
}
