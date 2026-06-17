import { ArrowDownOutlined, ArrowUpOutlined, EyeOutlined, MinusOutlined, WarningOutlined } from '@ant-design/icons';
import { Progress, Space, Tag, Tooltip, Typography } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { TradingAdviceItem } from '@/api/advisor';
import { dataQualityStatusLabel, dataQualityTagColor } from '@/components/DataTrustNotice';
import {
  adviceStrengthLabel,
  adviceStrengthTagColor,
  advisorDecisionLabel,
  overfitRiskLabel,
  overfitRiskTagColor,
  supportActionTagColor,
} from '@/utils/advisorDisplay';

const { Text } = Typography;

const ACTION_CONFIG = {
  buy: { icon: <ArrowUpOutlined />, text: '可关注增配', tagColor: 'red' },
  sell: { icon: <ArrowDownOutlined />, text: '可关注减配', tagColor: 'green' },
  hold: { icon: <MinusOutlined />, text: '继续观察', tagColor: 'default' },
  watch: { icon: <EyeOutlined />, text: '观察', tagColor: 'blue' },
};

function summarizeReasons(reasons: string[] | undefined, maxCount = 3): string[] {
  return (reasons || []).map((item) => String(item || '').trim()).filter(Boolean).slice(0, maxCount);
}

export function buildAdvisorAdviceColumns(viewMode: 'novice' | 'expert'): ColumnsType<TradingAdviceItem> {
  return [
    {
      title: '基金',
      key: 'fund',
      width: 150,
      render: (_, record) => (
        <div>
          <Text strong>{record.fund_code}</Text>
          <br />
          <Text type="secondary" style={{ fontSize: 12 }}>{record.fund_name || '-'}</Text>
        </div>
      ),
    },
    {
      title: '检查结论',
      dataIndex: 'action',
      key: 'action',
      width: 120,
      align: 'center',
      render: (_action: string, record) => {
        const config = ACTION_CONFIG[record.action as keyof typeof ACTION_CONFIG] || ACTION_CONFIG.hold;
        return (
          <Tag color={supportActionTagColor(record.support_action) || config.tagColor} icon={config.icon}>
            {advisorDecisionLabel(record)}
          </Tag>
        );
      },
    },
    {
      title: '强度',
      key: 'strength',
      width: 90,
      align: 'center',
      render: (_, record) => (
        <Tag color={adviceStrengthTagColor(record.action, record.confidence, record.strength)}>
          {adviceStrengthLabel(record.action, record.confidence, record.strength)}
        </Tag>
      ),
    },
    {
      title: '置信度',
      dataIndex: 'confidence',
      key: 'confidence',
      width: 90,
      align: 'center',
      render: (value: number) => (
        <Progress
          percent={Math.round(value * 100)}
          size="small"
          status={value > 0.6 ? 'success' : value > 0.3 ? 'normal' : 'exception'}
          format={(percent) => `${percent}%`}
        />
      ),
    },
    {
      title: '参考调整金额',
      dataIndex: 'suggested_amount',
      key: 'amount',
      width: 110,
      align: 'right',
      render: (value: number) => (value > 0 ? `¥${value.toLocaleString()}` : '-'),
    },
    {
      title: '执行方式',
      key: 'trade_plan',
      width: 110,
      align: 'center',
      render: (_, record) => {
        if (!record.trade_plan) return '-';
        const labelMap = {
          one_time: '一次性',
          batch: `分批${record.trade_plan.batch_count ? `(${record.trade_plan.batch_count}次)` : ''}`,
          fixed_investment: '定投',
          hold: '暂不操作',
        };
        return (
          <Tag color={record.trade_plan.execution_type === 'batch' ? 'blue' : record.trade_plan.execution_type === 'one_time' ? 'purple' : 'default'}>
            {labelMap[record.trade_plan.execution_type]}
          </Tag>
        );
      },
    },
    {
      title: '数据质量',
      key: 'data_quality',
      width: 95,
      align: 'center',
      render: (_, record) => {
        const quality = record.data_quality;
        if (!quality) return '-';
        return (
          <Tooltip title={(quality.warnings || []).join('；') || `质量分 ${Math.round((quality.score || 0) * 100)}`}>
            <Tag color={dataQualityTagColor(quality.status)}>{dataQualityStatusLabel(quality.status)}</Tag>
          </Tooltip>
        );
      },
    },
    {
      title: '过拟合',
      key: 'overfit_risk',
      width: 90,
      align: 'center',
      render: (_, record) => {
        const risk = record.overfit_risk;
        if (!risk) return '-';
        return (
          <Tooltip title={(risk.reasons || []).join('；') || `风险分 ${Math.round((risk.score || 0) * 100)}`}>
            <Tag color={overfitRiskTagColor(risk.level)}>{overfitRiskLabel(risk.level)}</Tag>
          </Tooltip>
        );
      },
    },
    ...(viewMode === 'expert'
      ? [
          {
            title: '综合评分',
            key: 'composite',
            width: 80,
            align: 'center' as const,
            render: (_: unknown, record: TradingAdviceItem) => {
              const score = record.scores.composite;
              return <Text style={{ color: score > 0.15 ? '#cf1322' : score < -0.15 ? '#3f8600' : '#666', fontWeight: 600 }}>{(score * 100).toFixed(0)}</Text>;
            },
          },
          {
            title: '审计',
            key: 'decision_audit',
            width: 90,
            align: 'center' as const,
            render: (_: unknown, record: TradingAdviceItem) => record.decision_audit ? (
              <Tooltip title={`增配关注阈值 ${record.decision_audit.effective_buy_threshold.toFixed(2)} / 减配关注阈值 ${record.decision_audit.effective_sell_threshold.toFixed(2)}；缺失信号源 ${record.decision_audit.missing_sources} 个`}>
                <Tag color={record.decision_audit.threshold_state === 'within_hold_band' ? 'default' : 'blue'}>
                  {record.decision_audit.threshold_state === 'above_buy_threshold'
                    ? '超过增配关注阈值'
                    : record.decision_audit.threshold_state === 'below_sell_threshold'
                      ? '跌破减配关注阈值'
                      : '观察区间'}
                </Tag>
              </Tooltip>
            ) : '-',
          },
          {
            title: '适当性',
            key: 'suitability',
            width: 90,
            align: 'center' as const,
            render: (_: unknown, record: TradingAdviceItem) => record.suitability
              ? <Tag color={record.suitability.matched ? 'green' : 'orange'}>{record.suitability.matched ? '匹配' : '需谨慎'}</Tag>
              : '-',
          },
          {
            title: '费用',
            key: 'fee',
            width: 80,
            align: 'right' as const,
            render: (_: unknown, record: TradingAdviceItem) => record.fee_estimate ? `¥${record.fee_estimate.estimated_fee.toFixed(0)}` : '-',
          },
          {
            title: '评分',
            key: 'scores',
            width: 200,
            render: (_: unknown, record: TradingAdviceItem) => (
              <Space size={2} wrap>
                <Tag color={record.scores.momentum > 0 ? 'red' : record.scores.momentum < 0 ? 'green' : 'default'}>动:{(record.scores.momentum * 100).toFixed(0)}</Tag>
                <Tag color={record.scores.strategy > 0 ? 'red' : record.scores.strategy < 0 ? 'green' : 'default'}>策:{(record.scores.strategy * 100).toFixed(0)}</Tag>
                <Tag color={record.scores.cross_sectional > 0 ? 'red' : record.scores.cross_sectional < 0 ? 'green' : 'default'}>截:{(record.scores.cross_sectional * 100).toFixed(0)}</Tag>
                {record.scores.technical !== 0 && <Tag color={record.scores.technical > 0 ? 'red' : record.scores.technical < 0 ? 'green' : 'default'}>技:{(record.scores.technical * 100).toFixed(0)}</Tag>}
                {record.scores.prediction !== 0 && <Tag color={record.scores.prediction > 0 ? 'red' : record.scores.prediction < 0 ? 'green' : 'default'}>预:{(record.scores.prediction * 100).toFixed(0)}</Tag>}
              </Space>
            ),
          },
        ]
      : []),
    {
      title: '理由',
      key: 'reasons',
      width: viewMode === 'expert' ? 300 : 360,
      render: (_, record) => (
        <div>
          {record.reasoning?.summary ? (
            <div style={{ fontSize: 12, marginBottom: 4, fontWeight: viewMode === 'novice' ? 600 : undefined }}>{record.reasoning.summary}</div>
          ) : (
            summarizeReasons(record.reasons, 3).map((reason, index) => <div key={index} style={{ fontSize: 12, marginBottom: 2 }}>• {reason}</div>)
          )}
          {!record.reasoning?.summary && viewMode === 'novice' && summarizeReasons(record.reasons, 3).length === 0 && <Text type="secondary">暂无结构化理由</Text>}
          {viewMode === 'novice' && summarizeReasons(record.reasons, 3).length > 0 && record.reasoning?.summary && (
            <div style={{ marginTop: 6 }}>
              {summarizeReasons(record.reasons, 3).map((reason, index) => <div key={index} style={{ fontSize: 12, marginBottom: 2 }}>• {reason}</div>)}
            </div>
          )}
          {record.reliability_adjustment && record.reliability_adjustment.multiplier < 1 && (
            <Tooltip title={record.reliability_adjustment.reason}>
              <Tag color="volcano" style={{ marginTop: 4 }}>防过拟合折扣 {(record.reliability_adjustment.multiplier * 100).toFixed(0)}%</Tag>
            </Tooltip>
          )}
          {record.validity?.valid_until && <Tag color="blue" style={{ marginTop: 4 }}>有效至 {record.validity.valid_until}</Tag>}
          {record.risk_warnings.length > 0 && (
            <Tooltip title={record.risk_warnings.join('；')}>
              <Tag color="orange" icon={<WarningOutlined />} style={{ marginTop: 4 }}>{viewMode === 'novice' ? `${record.risk_warnings.length}条风险` : `${record.risk_warnings.length}条风险提示`}</Tag>
            </Tooltip>
          )}
        </div>
      ),
    },
  ];
}
