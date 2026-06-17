import { useState } from 'react';
import {
  Typography,
  Card,
  Space,
  Statistic,
  Row,
  Col,
  Progress,
  Spin,
  Alert,
  Form,
  Input,
  Select,
  InputNumber,
  Switch,
  Button,
  Descriptions,
  Divider,
  Result,
  message,
} from 'antd';
import {
  DollarOutlined,
  ThunderboltOutlined,
  ApiOutlined,
  ClockCircleOutlined,
  SaveOutlined,
  SettingOutlined,
} from '@ant-design/icons';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { useAIUsage } from '../../api/ai';
import apiClient from '@/api/client';
import { useFeatureProfile } from '@/api/settings';

const { Title, Text } = Typography;

// ---------------------------------------------------------------------------
// AI Config Types & API
// ---------------------------------------------------------------------------

interface AIConfigResponse {
  ai_enabled: boolean;
  ai_default_provider: string;
  openai_api_key_masked: string;
  openai_base_url: string;
  openai_model: string;
  anthropic_api_key_masked: string;
  anthropic_model: string;
  llm_daily_budget_usd: number;
  llm_monthly_budget_usd: number;
}

interface AIConfigUpdate {
  ai_enabled?: boolean;
  ai_default_provider?: string;
  openai_api_key?: string;
  openai_base_url?: string;
  openai_model?: string;
  anthropic_api_key?: string;
  anthropic_model?: string;
  llm_daily_budget_usd?: number;
  llm_monthly_budget_usd?: number;
}

async function fetchAIConfig(): Promise<AIConfigResponse> {
  const { data } = await apiClient.get<AIConfigResponse>('/v1/settings/ai');
  return data;
}

async function updateAIConfig(payload: AIConfigUpdate): Promise<AIConfigResponse> {
  const { data } = await apiClient.put<AIConfigResponse>('/v1/settings/ai', payload);
  return data;
}

// ---------------------------------------------------------------------------
// AI Config Component
// ---------------------------------------------------------------------------

function AIConfigPanel() {
  const [form] = Form.useForm();
  const queryClient = useQueryClient();
  const [hasChanges, setHasChanges] = useState(false);

  const { data: config, isLoading } = useQuery({
    queryKey: ['settings', 'ai'],
    queryFn: fetchAIConfig,
  });

  const mutation = useMutation({
    mutationFn: updateAIConfig,
    onSuccess: (data) => {
      queryClient.setQueryData(['settings', 'ai'], data);
      message.success('配置已保存，重启后端服务后生效');
      setHasChanges(false);
    },
  });

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      const payload: AIConfigUpdate = {};

      if (values.ai_enabled !== undefined) payload.ai_enabled = values.ai_enabled;
      if (values.ai_default_provider) payload.ai_default_provider = values.ai_default_provider;
      if (values.openai_api_key) payload.openai_api_key = values.openai_api_key;
      if (values.openai_base_url) payload.openai_base_url = values.openai_base_url;
      if (values.openai_model) payload.openai_model = values.openai_model;
      if (values.anthropic_api_key) payload.anthropic_api_key = values.anthropic_api_key;
      if (values.anthropic_model) payload.anthropic_model = values.anthropic_model;
      if (values.llm_daily_budget_usd != null) payload.llm_daily_budget_usd = values.llm_daily_budget_usd;
      if (values.llm_monthly_budget_usd != null) payload.llm_monthly_budget_usd = values.llm_monthly_budget_usd;

      await mutation.mutateAsync(payload);
    } catch {
      // form validation error
    }
  };

  if (isLoading) {
    return (
      <Card title={<><SettingOutlined /> AI 配置</>}>
        <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
      </Card>
    );
  }

  return (
    <Card title={<><SettingOutlined /> AI 配置</>}>
      <Alert
        message="修改配置后需要重启后端服务才能生效"
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
      />

      {config && (
        <Descriptions size="small" column={{ xs: 1, sm: 2 }} style={{ marginBottom: 16 }} bordered>
          <Descriptions.Item label="状态">
            {config.ai_enabled ? <Text type="success">已启用</Text> : <Text type="secondary">未启用</Text>}
          </Descriptions.Item>
          <Descriptions.Item label="提供商">{config.ai_default_provider}</Descriptions.Item>
          <Descriptions.Item label="API Key">{config.openai_api_key_masked || '未配置'}</Descriptions.Item>
          <Descriptions.Item label="模型">{config.openai_model}</Descriptions.Item>
        </Descriptions>
      )}

      <Form
        form={form}
        layout="vertical"
        size="middle"
        initialValues={{
          ai_enabled: config?.ai_enabled ?? false,
          ai_default_provider: config?.ai_default_provider ?? 'openai_compat',
          openai_base_url: config?.openai_base_url ?? '',
          openai_model: config?.openai_model ?? '',
          anthropic_model: config?.anthropic_model ?? '',
          llm_daily_budget_usd: config?.llm_daily_budget_usd ?? 10,
          llm_monthly_budget_usd: config?.llm_monthly_budget_usd ?? 200,
        }}
        onValuesChange={() => setHasChanges(true)}
      >
        <Row gutter={16}>
          <Col xs={24} sm={8}>
            <Form.Item name="ai_enabled" label="启用 AI" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Col>
          <Col xs={24} sm={16}>
            <Form.Item name="ai_default_provider" label="提供商">
              <Select>
                <Select.Option value="openai_compat">OpenAI 兼容（DeepSeek / 智谱 / MiMo 等）</Select.Option>
                <Select.Option value="anthropic">Anthropic (Claude)</Select.Option>
              </Select>
            </Form.Item>
          </Col>
        </Row>

        <Divider orientation="left" plain>OpenAI 兼容</Divider>
        <Row gutter={16}>
          <Col xs={24} sm={8}>
            <Form.Item name="openai_api_key" label="API Key">
              <Input.Password placeholder="留空不修改" />
            </Form.Item>
          </Col>
          <Col xs={24} sm={8}>
            <Form.Item name="openai_base_url" label="Base URL">
              <Input placeholder="https://api.openai.com/v1" />
            </Form.Item>
          </Col>
          <Col xs={24} sm={8}>
            <Form.Item name="openai_model" label="模型">
              <Input placeholder="gpt-4o-mini" />
            </Form.Item>
          </Col>
        </Row>

        <Divider orientation="left" plain>Anthropic</Divider>
        <Row gutter={16}>
          <Col xs={24} sm={12}>
            <Form.Item name="anthropic_api_key" label="API Key">
              <Input.Password placeholder="留空不修改" />
            </Form.Item>
          </Col>
          <Col xs={24} sm={12}>
            <Form.Item name="anthropic_model" label="模型">
              <Input placeholder="claude-3-5-sonnet-latest" />
            </Form.Item>
          </Col>
        </Row>

        <Divider orientation="left" plain>预算</Divider>
        <Row gutter={16}>
          <Col xs={12} sm={6}>
            <Form.Item name="llm_daily_budget_usd" label="日预算 (USD)">
              <InputNumber min={0} step={1} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
          <Col xs={12} sm={6}>
            <Form.Item name="llm_monthly_budget_usd" label="月预算 (USD)">
              <InputNumber min={0} step={10} style={{ width: '100%' }} />
            </Form.Item>
          </Col>
        </Row>

        <Button
          type="primary"
          icon={<SaveOutlined />}
          onClick={handleSave}
          loading={mutation.isPending}
          disabled={!hasChanges}
        >
          保存配置
        </Button>
      </Form>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Usage Dashboard (simplified)
// ---------------------------------------------------------------------------

function UsageDashboard() {
  const { data: usage, isLoading, error } = useAIUsage(30);

  if (isLoading) return null;
  if (error || !usage) return null;

  // 如果没有任何调用，不显示用量面板
  if (usage.total_calls === 0) return null;

  const dailyPercent = usage.budget
    ? Math.min(100, (usage.budget.daily_spend_usd / usage.budget.daily_limit_usd) * 100)
    : 0;
  const monthlyPercent = usage.budget
    ? Math.min(100, (usage.budget.monthly_spend_usd / usage.budget.monthly_limit_usd) * 100)
    : 0;

  return (
    <Card title="用量统计（近 30 日）" size="small">
      <Row gutter={[16, 16]}>
        <Col xs={12} sm={6}>
          <Statistic title="调用次数" value={usage.total_calls} prefix={<ApiOutlined />} />
        </Col>
        <Col xs={12} sm={6}>
          <Statistic title="Token 消耗" value={usage.total_tokens} prefix={<ThunderboltOutlined />} />
        </Col>
        <Col xs={12} sm={6}>
          <Statistic title="费用 (USD)" value={usage.total_cost_usd} prefix={<DollarOutlined />} precision={4} />
        </Col>
        <Col xs={12} sm={6}>
          <Statistic title="平均延迟 (ms)" value={usage.avg_latency_ms ?? 0} prefix={<ClockCircleOutlined />} precision={0} />
        </Col>
      </Row>
      {usage.budget && (
        <Row gutter={[24, 16]} style={{ marginTop: 12 }}>
          <Col xs={24} sm={12}>
            <Text type="secondary">日预算</Text>
            <Progress percent={Number(dailyPercent.toFixed(1))} size="small" status={dailyPercent >= 90 ? 'exception' : 'active'} />
          </Col>
          <Col xs={24} sm={12}>
            <Text type="secondary">月预算</Text>
            <Progress percent={Number(monthlyPercent.toFixed(1))} size="small" status={monthlyPercent >= 90 ? 'exception' : 'active'} />
          </Col>
        </Row>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export function AIPage() {
  const navigate = useNavigate();
  const { data: featureProfile, isLoading, isError } = useFeatureProfile();
  const aiEnabled = featureProfile?.feature_ai === true;

  if (isLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 40 }}>
        <Spin />
      </div>
    );
  }

  if (isError || !aiEnabled) {
    return (
      <Result
        status="info"
        title="AI 助手未启用"
        subTitle="个人模式下默认隐藏 AI 助手入口。如需使用，可在环境变量中开启 AI 功能后重启服务。"
        extra={[
          <Button key="home" type="primary" onClick={() => navigate('/')}>
            返回首页
          </Button>,
          <Button key="settings" onClick={() => navigate('/settings')}>
            查看系统设置
          </Button>,
        ]}
      />
    );
  }

  return (
    <div>
      <Title level={3}>AI 助手</Title>
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <AIConfigPanel />
        <UsageDashboard />
      </Space>
    </div>
  );
}
