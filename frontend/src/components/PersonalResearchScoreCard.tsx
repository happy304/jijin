import { Alert, Card, Col, Empty, Progress, Row, Space, Tag, Typography } from 'antd';

const { Text } = Typography;

export interface PersonalResearchScore {
  total: number;
  level: 'focus' | 'watch' | 'caution' | 'insufficient';
  dimensions: Array<{ key: string; label: string; score: number; weight: number; reason: string }>;
  explanations: string[];
}

function scoreLevelConfig(level: PersonalResearchScore['level']) {
  const config = {
    focus: { label: '优先研究', color: 'green' },
    watch: { label: '可观察', color: 'blue' },
    caution: { label: '需谨慎', color: 'orange' },
    insufficient: { label: '数据不足', color: 'default' },
  };
  return config[level];
}

export function PersonalResearchScoreCard({ score }: { score: PersonalResearchScore | null }) {
  return (
    <Card title="个人研究评分（轻量版）" style={{ marginBottom: 16 }}>
      {!score ? (
        <Empty description="暂无评分数据" />
      ) : (
        <Space direction="vertical" size={16} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="该评分仅用于个人基金研究辅助"
            description="评分基于近一年净值、数据质量、费率和持仓集中度等已加载信息进行轻量估算，不预测未来收益，不构成投资建议或交易指令。"
          />
          <Row gutter={[16, 16]} align="middle">
            <Col xs={24} md={8}>
              <div style={{ textAlign: 'center' }}>
                <Progress
                  type="dashboard"
                  percent={Math.round(score.total)}
                  status={score.level === 'caution' ? 'exception' : score.level === 'insufficient' ? 'normal' : 'success'}
                />
                <div style={{ marginTop: 8 }}>
                  <Tag color={scoreLevelConfig(score.level).color}>
                    {scoreLevelConfig(score.level).label}
                  </Tag>
                </div>
              </div>
            </Col>
            <Col xs={24} md={16}>
              <Row gutter={[12, 12]}>
                {score.dimensions.map((item) => (
                  <Col xs={24} sm={12} key={item.key}>
                    <Card size="small" variant="borderless" style={{ background: '#fafafa' }}>
                      <Space direction="vertical" size={4} style={{ width: '100%' }}>
                        <Space style={{ justifyContent: 'space-between', width: '100%' }}>
                          <Text strong>{item.label}</Text>
                          <Text type="secondary">权重 {item.weight}%</Text>
                        </Space>
                        <Progress percent={Math.round(item.score)} size="small" />
                        <Text type="secondary" style={{ fontSize: 12 }}>{item.reason}</Text>
                      </Space>
                    </Card>
                  </Col>
                ))}
              </Row>
            </Col>
          </Row>
          <Alert
            type={score.level === 'caution' || score.level === 'insufficient' ? 'warning' : 'success'}
            showIcon
            message="评分解读"
            description={
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {score.explanations.map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            }
          />
        </Space>
      )}
    </Card>
  );
}
