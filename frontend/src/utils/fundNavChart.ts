import type { EChartsOption } from 'echarts';
import type { NavResponse } from '@/api/funds';

export function buildFundNavChartOption(navData: NavResponse | undefined): EChartsOption {
  if (!navData?.records?.length) {
    return {};
  }

  const dates = navData.records.map((record) => record.trade_date);
  const unitNavs = navData.records.map((record) => (
    record.unit_nav ? parseFloat(record.unit_nav) : null
  ));
  const accumNavs = navData.records.map((record) => (
    record.accum_nav ? parseFloat(record.accum_nav) : null
  ));

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
    },
    legend: {
      data: ['单位净值', '累计净值'],
      top: 0,
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '3%',
      containLabel: true,
    },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: {
        rotate: 30,
        fontSize: 11,
      },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLabel: {
        formatter: '{value}',
      },
    },
    series: [
      {
        name: '单位净值',
        type: 'line',
        data: unitNavs,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2 },
        itemStyle: { color: '#1890ff' },
      },
      {
        name: '累计净值',
        type: 'line',
        data: accumNavs,
        smooth: true,
        showSymbol: false,
        lineStyle: { width: 2, type: 'dashed' },
        itemStyle: { color: '#52c41a' },
      },
    ],
    dataZoom: [
      {
        type: 'inside',
        start: 0,
        end: 100,
      },
      {
        type: 'slider',
        start: 0,
        end: 100,
      },
    ],
  };
}
