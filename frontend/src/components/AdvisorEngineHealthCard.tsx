import { Alert, Card, Col, Divider, Row, Space, Statistic, Tag, Typography } from 'antd';
import { useEngineHealth } from '@/api/advisor';

const { Text } = Typography;

const statusConfig = {
  healthy: { color: '#3f8600', icon: '🟢', text: '健康' },
  degraded: { color: '#d4b106', icon: '🟡', text: '信号减弱' },
  unhealthy: { color: '#cf1322', icon: '🔴', text: '信号失效' },
  insufficient_data: { color: '#666', icon: '⚪', text: '数据不足' },
  unknown: { color: '#666', icon: '❓', text: '未知' },
};

const trendConfig = {
  improving: { color: '#3f8600', text: '↑ 改善中' },
  stable: { color: '#666', text: '→ 稳定' },
  declining: { color: '#d4b106', text: '↓ 下降中' },
  critical: { color: '#cf1322', text: '⚠ 严重衰减' },
};

export function AdvisorEngineHealthCard() {
  const { data: health, isLoading } = useEngineHealth();

  if (isLoading) return <Card loading style={{ marginBottom: 16 }} />;
  if (!health) return null;

  const cfg = statusConfig[health.status] || statusConfig.unknown;
  const trendCfg = trendConfig[health.rolling_ic.trend] || trendConfig.stable;
  const queueHealth = health.runtime_health?.queue;
  const queueStatus = queueHealth?.status || 'unknown';
  const queueBacklog = Object.values(queueHealth?.queues || {}).reduce<number>((sum, value) => sum + (value || 0), 0);

  return (
    <Card
      title={<span>{cfg.icon} 引擎健康度: <Text style={{ color: cfg.color, fontWeight: 600 }}>{cfg.text}</Text></span>}
      style={{ marginBottom: 16 }}
      extra={health.last_validated ? <Text type="secondary" style={{ fontSize: 12 }}>上次验证: {health.last_validated}</Text> : null}
    >
      <Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>{health.status_reason}</Text>

      {queueHealth && (
        <Alert
          style={{ marginBottom: 12 }}
          type={queueStatus === 'healthy' ? 'success' : queueStatus === 'unknown' ? 'info' : 'warning'}
          showIcon
          message="运行时队列状态"
          description={
            <Space size={6} wrap>
              <Tag color={queueStatus === 'healthy' ? 'green' : 'orange'}>{queueStatus}</Tag>
              <Tag>Redis {queueHealth.redis_available ? '可用' : '不可用'}</Tag>
              <Tag>积压 {queueBacklog}</Tag>
              {Object.entries(queueHealth.queues || {}).map(([queue, size]) => (
                <Tag key={queue}>{queue}: {size ?? '未知'}</Tag>
              ))}
              {(queueHealth.warnings || []).map((warning) => (
                <Text key={warning} type="warning" style={{ fontSize: 12 }}>{warning}</Text>
              ))}
            </Space>
          }
        />
      )}

      <Row gutter={16}>
        <Col span={6}>
          <Statistic
            title="滚动 IC (20日)"
            value={health.rolling_ic.ic_20d ?? '-'}
            precision={4}
            valueStyle={{ color: (health.rolling_ic.ic_20d ?? 0) >= health.thresholds.ic_healthy ? '#3f8600' : (health.rolling_ic.ic_20d ?? 0) >= health.thresholds.ic_degraded ? '#d4b106' : '#cf1322' }}
          />
        </Col>
        <Col span={4}>
          <Statistic title="样本量" value={health.rolling_ic.samples} suffix={`/${health.thresholds.min_samples}`} />
        </Col>
        <Col span={4}>
          <Statistic title="IC 趋势" value={trendCfg.text} valueStyle={{ color: trendCfg.color, fontSize: 14 }} />
        </Col>
        <Col span={5}>
          <Statistic
            title="增配关注命中率"
            value={health.hit_rates.buy != null ? `${(health.hit_rates.buy * 100).toFixed(1)}%` : '-'}
            suffix={health.hit_rates.buy_count > 0 ? `(${health.hit_rates.buy_count})` : ''}
          />
        </Col>
        <Col span={5}>
          <Statistic
            title="减配关注命中率"
            value={health.hit_rates.sell != null ? `${(health.hit_rates.sell * 100).toFixed(1)}%` : '-'}
            suffix={health.hit_rates.sell_count > 0 ? `(${health.hit_rates.sell_count})` : ''}
          />
        </Col>
      </Row>

      <Divider style={{ margin: '12px 0' }} />
      <Text type="secondary" style={{ fontSize: 11 }}>
        判断标准: IC ≥ {health.thresholds.ic_healthy} 为健康，IC &lt; {health.thresholds.ic_degraded} 为失效。
        命中率 &gt; {(health.thresholds.hit_rate_healthy * 100).toFixed(0)}% 表示方向判断优于随机。
        每周日 03:00 自动验证，IC 衰减时自动告警。
      </Text>
    </Card>
  );
}
