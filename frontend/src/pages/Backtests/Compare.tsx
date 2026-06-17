import { useMemo } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import {
  Typography,
  Card,
  Spin,
  Alert,
  Button,
  Space,
  Table,
  Empty,
  Tag,
} from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import type { ColumnsType } from 'antd/es/table';
import { useQueries } from '@tanstack/react-query';
import apiClient from '@/api/client';
import {
  type BacktestResult,
  type EquityResponse,
} from '@/api/backtests';

const { Title } = Typography;

// 策略曲线颜色
const COLORS = [
  '#1890ff',
  '#52c41a',
  '#faad14',
  '#722ed1',
  '#eb2f96',
  '#13c2c2',
  '#fa541c',
  '#2f54eb',
];

// ---------------------------------------------------------------------------
// 指标定义
// ---------------------------------------------------------------------------

interface MetricDef {
  key: string;
  label: string;
  suffix?: string;
  precision: number;
  higherIsBetter?: boolean;
  /** Whether the raw value is a ratio (0.15 = 15%) that needs *100 for display */
  multiply100?: boolean;
}

const METRICS: MetricDef[] = [
  { key: 'total_return', label: '总收益率', suffix: '%', precision: 2, higherIsBetter: true, multiply100: true },
  { key: 'annualized_return', label: '年化收益率', suffix: '%', precision: 2, higherIsBetter: true, multiply100: true },
  { key: 'sharpe', label: 'Sharpe 比率', precision: 3, higherIsBetter: true },
  { key: 'max_drawdown', label: '最大回撤', suffix: '%', precision: 2, higherIsBetter: false, multiply100: true },
  { key: 'volatility', label: '年化波动率', suffix: '%', precision: 2, higherIsBetter: false, multiply100: true },
  { key: 'sortino', label: 'Sortino 比率', precision: 3, higherIsBetter: true },
  { key: 'calmar', label: 'Calmar 比率', precision: 3, higherIsBetter: true },
  { key: 'win_rate', label: '胜率', suffix: '%', precision: 1, higherIsBetter: true, multiply100: true },
];

// ---------------------------------------------------------------------------
// 主组件
// ---------------------------------------------------------------------------

export function ComparePage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const ids = useMemo(() => {
    const idsParam = searchParams.get('ids') || '';
    return idsParam
      .split(',')
      .map((s) => parseInt(s.trim(), 10))
      .filter((n) => !isNaN(n) && n > 0);
  }, [searchParams]);

  if (ids.length < 2) {
    return (
      <div style={{ padding: 24 }}>
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate('/backtests')}
          style={{ marginBottom: 16 }}
        >
          返回列表
        </Button>
        <Alert
          type="warning"
          message="参数不足"
          description="请选择至少 2 个已完成的回测进行对比（最多 5 个）。"
          showIcon
        />
      </div>
    );
  }

  if (ids.length > 5) {
    return (
      <div style={{ padding: 24 }}>
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate('/backtests')}
          style={{ marginBottom: 16 }}
        >
          返回列表
        </Button>
        <Alert
          type="warning"
          message="选择过多"
          description="最多支持 5 个回测同时对比，请减少选择数量。"
          showIcon
        />
      </div>
    );
  }

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/backtests')}>
          返回列表
        </Button>
        <Title level={3} style={{ margin: 0 }}>
          策略对比
        </Title>
        <Tag color="blue">{ids.length} 个策略</Tag>
      </Space>

      <CompareContent ids={ids} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 对比内容（加载多个回测数据）
// ---------------------------------------------------------------------------

function CompareContent({ ids }: { ids: number[] }) {
  // 使用 useQueries 并发加载所有回测状态和权益曲线（避免在回调中调用 hooks）
  const statusQueries = useQueries({
    queries: ids.map((id) => ({
      queryKey: ['backtest', 'status', id],
      queryFn: async () => {
        const { data } = await apiClient.get<BacktestResult>(`/v1/backtests/${id}`);
        return data;
      },
      enabled: !!id,
    })),
  });

  const equityQueries = useQueries({
    queries: ids.map((id) => ({
      queryKey: ['backtest', 'equity', id],
      queryFn: async () => {
        const { data } = await apiClient.get<EquityResponse>(`/v1/backtests/${id}/equity`);
        return data;
      },
      enabled: !!id,
    })),
  });

  const isLoadingStatus = statusQueries.some((q) => q.isLoading);
  const isLoadingEquity = equityQueries.some((q) => q.isLoading);
  const isLoading = isLoadingStatus || isLoadingEquity;

  const hasStatusError = statusQueries.some((q) => q.isError);
  const hasEquityError = equityQueries.some((q) => q.isError);

  const backtests: (BacktestResult | undefined)[] = statusQueries.map((q) => q.data);
  const equities: (EquityResponse | undefined)[] = equityQueries.map((q) => q.data);

  // 检查是否所有回测都已完成
  const allDone = backtests.every((b) => b?.status === 'done');
  const notDoneIds = ids.filter((_, i) => backtests[i] && backtests[i]?.status !== 'done');

  if (isLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 100 }}>
        <Spin size="large" tip="加载回测数据中..." />
      </div>
    );
  }

  if (hasStatusError) {
    return (
      <Alert
        type="error"
        message="加载失败"
        description="部分回测数据加载失败，请检查回测 ID 是否有效。"
        showIcon
      />
    );
  }

  if (!allDone && notDoneIds.length > 0) {
    return (
      <Alert
        type="warning"
        message="部分回测未完成"
        description={`以下回测尚未完成，无法对比：${notDoneIds.join(', ')}。请选择已完成的回测。`}
        showIcon
      />
    );
  }

  return (
    <>
      {/* 多策略曲线叠加 */}
      <CompareEquityChart
        ids={ids}
        equities={equities}
        isLoading={isLoadingEquity}
        hasError={hasEquityError}
      />

      {/* 关键指标并排对比表 */}
      <CompareMetricsTable ids={ids} backtests={backtests} />
    </>
  );
}

// ---------------------------------------------------------------------------
// 多策略曲线叠加图表
// ---------------------------------------------------------------------------

function CompareEquityChart({
  ids,
  equities,
  isLoading,
  hasError,
}: {
  ids: number[];
  equities: (EquityResponse | undefined)[];
  isLoading: boolean;
  hasError: boolean;
}) {
  const chartOption: EChartsOption = useMemo(() => {
    const validEquities = equities.filter(
      (e): e is EquityResponse => !!e && !!e.records?.length,
    );

    if (validEquities.length === 0) return {};

    // 收集所有日期并排序
    const allDatesSet = new Set<string>();
    validEquities.forEach((eq) => {
      eq.records.forEach((r) => allDatesSet.add(r.trade_date));
    });
    const allDates = Array.from(allDatesSet).sort();

    // 为每个策略生成归一化净值序列（以初始值为 1）
    const series = ids.map((id, idx) => {
      const equity = equities[idx];
      if (!equity?.records?.length) return null;

      const records = equity.records;
      const initialEquity = records[0].equity;

      // 创建日期到净值的映射
      const dateMap = new Map<string, number>();
      records.forEach((r) => {
        dateMap.set(r.trade_date, r.equity / initialEquity);
      });

      // 按全局日期序列填充数据
      const data = allDates.map((d) => dateMap.get(d) ?? null);

      const label = `回测 #${id}`;

      return {
        name: label,
        type: 'line' as const,
        data,
        smooth: true,
        showSymbol: false,
        connectNulls: true,
        lineStyle: { width: 2, color: COLORS[idx % COLORS.length] },
        itemStyle: { color: COLORS[idx % COLORS.length] },
      };
    }).filter((s): s is NonNullable<typeof s> => s !== null);

    return {
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        formatter: (params: unknown) => {
          if (!Array.isArray(params) || params.length === 0) return '';
          const items = params as Array<{ axisValue: string; marker: string; seriesName: string; value: number | null }>;
          let html = `<div style="font-weight:bold;margin-bottom:4px">${items[0].axisValue}</div>`;
          items.forEach((p) => {
            if (p.value != null) {
              const pct = ((p.value - 1) * 100).toFixed(2);
              html += `<div>${p.marker} ${p.seriesName}: ${pct}%</div>`;
            }
          });
          return html;
        },
      },
      legend: {
        data: ids.map((id) => `回测 #${id}`),
        top: 0,
      },
      grid: {
        left: '3%',
        right: '4%',
        bottom: '12%',
        top: '12%',
        containLabel: true,
      },
      xAxis: {
        type: 'category',
        data: allDates,
        axisLabel: { rotate: 30, fontSize: 10 },
      },
      yAxis: {
        type: 'value',
        name: '归一化净值',
        scale: true,
        axisLabel: {
          formatter: (v: number) => ((v - 1) * 100).toFixed(0) + '%',
        },
      },
      series,
      dataZoom: [
        { type: 'inside', start: 0, end: 100 },
        { type: 'slider', start: 0, end: 100, bottom: 0 },
      ],
    };
  }, [ids, equities]);

  return (
    <Card title="净值曲线对比" style={{ marginBottom: 16 }}>
      {isLoading ? (
        <div style={{ textAlign: 'center', padding: 60 }}>
          <Spin tip="加载净值数据中..." />
        </div>
      ) : hasError ? (
        <Alert type="error" message="部分净值数据加载失败" showIcon />
      ) : !equities.some((e) => e?.records?.length) ? (
        <Empty description="暂无净值数据" />
      ) : (
        <ReactECharts option={chartOption} style={{ height: 450 }} notMerge />
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// 关键指标并排对比表
// ---------------------------------------------------------------------------

function CompareMetricsTable({
  ids,
  backtests,
}: {
  ids: number[];
  backtests: (BacktestResult | undefined)[];
}) {
  // 构建表格数据：每行是一个指标，每列是一个策略
  interface MetricRow {
    key: string;
    metric: string;
    [runKey: string]: string | number | undefined;
  }

  const dataSource: MetricRow[] = METRICS.map((m) => {
    const row: MetricRow = {
      key: m.key,
      metric: m.label,
    };

    ids.forEach((id, idx) => {
      const bt = backtests[idx];
      const raw = bt?.metrics?.[m.key as keyof typeof bt.metrics];
      // Only use numeric values (skip objects like sharpe_inference)
      row[`run_${id}`] = typeof raw === 'number' ? raw : undefined;
    });

    return row;
  });

  // 找出每行的最优值
  const bestValues = useMemo(() => {
    const result: Record<string, number | undefined> = {};

    METRICS.forEach((m) => {
      const values = ids
        .map((_id, idx) => {
          const bt = backtests[idx];
          const v = bt?.metrics?.[m.key as keyof typeof bt.metrics];
          return typeof v === 'number' ? v : undefined;
        })
        .filter((v): v is number => v !== undefined);

      if (values.length === 0) {
        result[m.key] = undefined;
        return;
      }

      if (m.higherIsBetter === true) {
        result[m.key] = Math.max(...values);
      } else if (m.higherIsBetter === false) {
        // 对于"越小越好"的指标（如回撤、波动率），取绝对值最小的
        result[m.key] = values.reduce((best, v) =>
          Math.abs(v) < Math.abs(best) ? v : best,
        );
      }
    });

    return result;
  }, [ids, backtests]);

  const columns: ColumnsType<MetricRow> = [
    {
      title: '指标',
      dataIndex: 'metric',
      key: 'metric',
      fixed: 'left',
      width: 130,
      render: (text: string) => <strong>{text}</strong>,
    },
    ...ids.map((id, idx) => ({
      title: (
        <span style={{ color: COLORS[idx % COLORS.length] }}>
          回测 #{id}
        </span>
      ),
      dataIndex: `run_${id}`,
      key: `run_${id}`,
      width: 130,
      align: 'right' as const,
      render: (value: number | undefined, record: MetricRow) => {
        if (value == null) return <span style={{ color: '#999' }}>-</span>;

        const metricDef = METRICS.find((m) => m.key === record.key);
        if (!metricDef) return value;

        const displayValue = metricDef.multiply100 ? value * 100 : value;
        const formatted = displayValue.toFixed(metricDef.precision) + (metricDef.suffix || '');
        const isBest = bestValues[record.key] === value && ids.length > 1;

        return (
          <span style={{ fontWeight: isBest ? 'bold' : 'normal', color: isBest ? '#1890ff' : undefined }}>
            {formatted}
            {isBest && ' ★'}
          </span>
        );
      },
    })),
  ];

  return (
    <Card title="关键指标对比" style={{ marginBottom: 16 }}>
      {backtests.every((b) => !b?.metrics) ? (
        <Empty description="暂无指标数据" />
      ) : (
        <Table<MetricRow>
          columns={columns}
          dataSource={dataSource}
          pagination={false}
          size="middle"
          scroll={{ x: 130 + ids.length * 130 }}
          bordered
        />
      )}
    </Card>
  );
}
