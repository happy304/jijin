import { useEffect, useState } from 'react';
import { Typography, Card, Descriptions, Table, Alert, Space, Tag, Button, Select, Switch, message } from 'antd';
import { useQuery } from '@tanstack/react-query';
import type { ColumnsType } from 'antd/es/table';
import apiClient from '@/api/client';
import { useMetricDefinitions, type MetricDefinition } from '@/api/meta';
import { useFeatureProfile, useUpdateFeatureProfile, type ScheduleTaskProfile } from '@/api/settings';
import { useAppStore } from '@/stores';

const { Title, Text } = Typography;

function scheduleModeLabel(mode: string | undefined): string {
  const labels: Record<string, string> = {
    light: '轻量模式',
    research: '研究模式',
    full: '完整模式',
  };
  return labels[mode || ''] || mode || '-';
}

function scheduleModeDescription(mode: string | undefined): string {
  const descriptions: Record<string, string> = {
    light: '个人默认：保留净值、元数据、基准、分红和备份等核心数据任务。',
    research: '研究增强：在轻量模式基础上增加基金发现、截面评分、策略信号、IC 验证和持仓更新。',
    full: '完整调度：启用全部后台任务，适合高级研究或生产化运维。',
  };
  return descriptions[mode || ''] || '未知调度模式，请检查后端配置。';
}

function booleanTag(enabled: boolean | undefined, enabledText = '已启用', disabledText = '已关闭') {
  return <Tag color={enabled ? 'green' : 'default'}>{enabled ? enabledText : disabledText}</Tag>;
}

interface VersionInfo {
  name: string;
  version: string;
  environment: string;
}

async function fetchVersion(): Promise<VersionInfo> {
  const { data } = await apiClient.get<VersionInfo>('/v1/version');
  return data;
}

export function SettingsPage() {
  const { data: version } = useQuery({
    queryKey: ['version'],
    queryFn: fetchVersion,
  });
  const { data: metricDefinitions, isLoading: metricLoading, isError: metricError } = useMetricDefinitions();
  const { data: featureProfile, isLoading: featureLoading, isError: featureError } = useFeatureProfile();
  const updateFeatureProfileMutation = useUpdateFeatureProfile();
  const menuModeOverride = useAppStore((s) => s.menuModeOverride);
  const setMenuModeOverride = useAppStore((s) => s.setMenuModeOverride);
  const aiMenuVisibleOverride = useAppStore((s) => s.aiMenuVisibleOverride);
  const setAiMenuVisibleOverride = useAppStore((s) => s.setAiMenuVisibleOverride);
  const [personalModeDraft, setPersonalModeDraft] = useState(false);
  const [featureAiDraft, setFeatureAiDraft] = useState(false);
  const [advisorGovernanceDraft, setAdvisorGovernanceDraft] = useState(false);
  const [fullMonitoringDraft, setFullMonitoringDraft] = useState(false);
  const [scheduleModeDraft, setScheduleModeDraft] = useState<'light' | 'research' | 'full'>('light');

  useEffect(() => {
    if (!featureProfile) return;
    setPersonalModeDraft(featureProfile.personal_mode);
    setFeatureAiDraft(featureProfile.feature_ai);
    setAdvisorGovernanceDraft(featureProfile.feature_advisor_governance);
    setFullMonitoringDraft(featureProfile.feature_full_monitoring);
    setScheduleModeDraft(featureProfile.schedule_mode === 'research' || featureProfile.schedule_mode === 'full' ? featureProfile.schedule_mode : 'light');
  }, [featureProfile]);

  const handleSaveFeatureProfile = async () => {
    try {
      await updateFeatureProfileMutation.mutateAsync({
        personal_mode: personalModeDraft,
        feature_ai: featureAiDraft,
        feature_advisor_governance: advisorGovernanceDraft,
        feature_full_monitoring: fullMonitoringDraft,
        schedule_mode: scheduleModeDraft,
      });
      message.success('功能开关已保存到 .env。部分后台调度变更需重启 Celery Beat 后生效。');
    } catch (error) {
      message.error(`保存失败：${error instanceof Error ? error.message : '未知错误'}`);
    }
  };

  const scheduleTaskColumns: ColumnsType<ScheduleTaskProfile> = [
    {
      title: '任务名称',
      dataIndex: 'name',
      key: 'name',
      width: 240,
      render: (name: string, record) => (
        <Space direction="vertical" size={0}>
          <Text strong>{name}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{record.task}</Text>
        </Space>
      ),
    },
    {
      title: '队列',
      dataIndex: 'queue',
      key: 'queue',
      width: 120,
      render: (queue: string | null) => queue ? <Tag>{queue}</Tag> : '-',
    },
    {
      title: '状态',
      dataIndex: 'enabled',
      key: 'enabled',
      width: 100,
      render: (enabled: boolean) => (
        <Tag color={enabled ? 'green' : 'default'}>{enabled ? '已启用' : '未启用'}</Tag>
      ),
    },
  ];

  const metricColumns: ColumnsType<MetricDefinition> = [
    {
      title: '指标',
      dataIndex: 'name',
      key: 'name',
      width: 140,
      render: (name: string, record) => (
        <Space direction="vertical" size={0}>
          <Text strong>{name}</Text>
          <Text type="secondary" style={{ fontSize: 12 }}>{record.key}</Text>
        </Space>
      ),
    },
    {
      title: '公式',
      dataIndex: 'formula',
      key: 'formula',
      render: (value: string) => <Text code>{value}</Text>,
    },
    {
      title: '符号/方向',
      dataIndex: 'sign',
      key: 'sign',
      width: 220,
    },
    {
      title: '数据不足处理',
      dataIndex: 'insufficient_data',
      key: 'insufficient_data',
      width: 260,
    },
    {
      title: '用途',
      dataIndex: 'usage',
      key: 'usage',
      width: 220,
    },
  ];

  return (
    <div>
      <Title level={3}>系统设置</Title>

      <Card title="系统信息" style={{ marginBottom: 16 }}>
        <Descriptions column={{ xs: 1, md: 3 }} bordered size="small">
          <Descriptions.Item label="应用名称">{version?.name ?? '-'}</Descriptions.Item>
          <Descriptions.Item label="版本">{version?.version ?? '-'}</Descriptions.Item>
          <Descriptions.Item label="运行环境">{version?.environment ?? '-'}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card
        title="个人使用配置"
        loading={featureLoading}
        extra={featureProfile && (
          <Space>
            <Tag color={featureProfile.personal_mode ? 'green' : 'blue'}>
              {featureProfile.personal_mode ? '个人模式' : '高级模式'}
            </Tag>
            <Tag color={featureProfile.schedule_mode === 'light' ? 'green' : featureProfile.schedule_mode === 'research' ? 'blue' : 'purple'}>
              {scheduleModeLabel(featureProfile.schedule_mode)}
            </Tag>
          </Space>
        )}
        style={{ marginBottom: 16 }}
      >
        {featureError ? (
          <Alert type="warning" showIcon message="个人使用配置加载失败" description="当前页面仍可使用，导航会回退到前端默认个人模式。" />
        ) : (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              type="info"
              showIcon
              message="这里现在可以直接设置功能入口"
              description="本地显示设置会立即影响左侧导航；保存到后端会写入 .env，刷新/重启后仍保留。调度模式变更需要重启 Celery Beat 才会完全影响后台定时任务。"
            />
            <Card size="small" title="可操作设置">
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <Space wrap>
                  <Text strong>左侧导航显示：</Text>
                  <Select
                    style={{ width: 180 }}
                    value={menuModeOverride}
                    onChange={setMenuModeOverride}
                    options={[
                      { value: 'server', label: '跟随后端配置' },
                      { value: 'full', label: '完整模式（显示全部）' },
                      { value: 'personal', label: '个人模式（精简）' },
                    ]}
                  />
                  <Text type="secondary">当前实际：{menuModeOverride === 'full' ? '完整模式' : menuModeOverride === 'personal' ? '个人模式' : featureProfile?.personal_mode ? '个人模式' : '完整模式'}</Text>
                </Space>
                <Space wrap>
                  <Text strong>AI 菜单入口：</Text>
                  <Select
                    style={{ width: 180 }}
                    value={aiMenuVisibleOverride === null ? 'server' : aiMenuVisibleOverride ? 'show' : 'hide'}
                    onChange={(value) => setAiMenuVisibleOverride(value === 'server' ? null : value === 'show')}
                    options={[
                      { value: 'server', label: '跟随后端配置' },
                      { value: 'show', label: '强制显示' },
                      { value: 'hide', label: '强制隐藏' },
                    ]}
                  />
                  <Text type="secondary">本地设置即时生效。</Text>
                </Space>
                <Space wrap>
                  <Text strong>保存到后端 .env：</Text>
                  <span>个人模式</span><Switch checked={personalModeDraft} onChange={setPersonalModeDraft} />
                  <span>AI</span><Switch checked={featureAiDraft} onChange={setFeatureAiDraft} />
                  <span>Advisor 高级治理</span><Switch checked={advisorGovernanceDraft} onChange={setAdvisorGovernanceDraft} />
                  <span>完整监控</span><Switch checked={fullMonitoringDraft} onChange={setFullMonitoringDraft} />
                  <span>调度</span>
                  <Select
                    style={{ width: 120 }}
                    value={scheduleModeDraft}
                    onChange={setScheduleModeDraft}
                    options={[
                      { value: 'light', label: '轻量' },
                      { value: 'research', label: '研究' },
                      { value: 'full', label: '完整' },
                    ]}
                  />
                  <Button type="primary" loading={updateFeatureProfileMutation.isPending} onClick={handleSaveFeatureProfile}>保存设置</Button>
                </Space>
              </Space>
            </Card>
            <Descriptions column={{ xs: 1, md: 2 }} bordered size="small">
              <Descriptions.Item label="个人模式">
                {booleanTag(featureProfile?.personal_mode, '已启用', '已关闭')}
                <Text type="secondary" style={{ marginLeft: 8 }}>
                  {featureProfile?.personal_mode ? '默认收敛导航与高级入口' : '展示更多研究入口'}
                </Text>
              </Descriptions.Item>
              <Descriptions.Item label="调度模式">
                <Space direction="vertical" size={2}>
                  <Tag color={featureProfile?.schedule_mode === 'light' ? 'green' : featureProfile?.schedule_mode === 'research' ? 'blue' : 'purple'}>
                    {scheduleModeLabel(featureProfile?.schedule_mode)}
                  </Tag>
                  <Text type="secondary" style={{ fontSize: 12 }}>{scheduleModeDescription(featureProfile?.schedule_mode)}</Text>
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="AI 助手入口">{booleanTag(featureProfile?.feature_ai)}</Descriptions.Item>
              <Descriptions.Item label="Advisor 高级治理">{booleanTag(featureProfile?.feature_advisor_governance)}</Descriptions.Item>
              <Descriptions.Item label="完整监控入口">{booleanTag(featureProfile?.feature_full_monitoring)}</Descriptions.Item>
              <Descriptions.Item label="启用调度任务">
                <Tag color="green">{featureProfile?.schedule_enabled_tasks?.length ?? 0} 个</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="隐藏/未启用任务">
                <Tag>{featureProfile?.schedule_disabled_tasks?.length ?? 0} 个</Tag>
              </Descriptions.Item>
            </Descriptions>

            <Card size="small" title="当前调度任务清单">
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 12 }}
                message="这里展示后端按当前 SCHEDULE_MODE 实际加载的 Celery Beat 任务。"
                description="light 模式只保留个人日常数据更新和备份任务；research/full 会逐步增加研究、诊断或运维任务。"
              />
              <Table<ScheduleTaskProfile>
                rowKey="name"
                columns={scheduleTaskColumns}
                dataSource={featureProfile?.schedule_enabled_tasks || []}
                pagination={false}
                size="small"
              />
            </Card>

            {featureProfile?.schedule_disabled_tasks?.length ? (
              <Card size="small" title="当前模式未启用的高级调度任务">
                <Table<ScheduleTaskProfile>
                  rowKey="name"
                  columns={scheduleTaskColumns}
                  dataSource={featureProfile.schedule_disabled_tasks}
                  pagination={{ pageSize: 8, showSizeChanger: false }}
                  size="small"
                />
              </Card>
            ) : null}
          </Space>
        )}
      </Card>

      <Card
        title="指标口径说明"
        extra={metricDefinitions && (
          <Space>
            <Tag color="blue">版本 {metricDefinitions.metric_version}</Tag>
            <Tag>年化频率 {metricDefinitions.frequency}</Tag>
          </Space>
        )}
        style={{ marginBottom: 16 }}
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="同一指标在因子、回测、模拟和报告中应尽量复用统一口径。"
          description="这里展示后端当前发布的核心收益、风险和风险调整指标定义，便于回测结果审计和长期跟踪时核对。"
        />
        {metricError ? (
          <Alert type="error" showIcon message="指标口径说明加载失败" />
        ) : (
          <Table<MetricDefinition>
            rowKey="key"
            columns={metricColumns}
            dataSource={metricDefinitions?.definitions || []}
            loading={metricLoading}
            pagination={false}
            size="small"
            scroll={{ x: 1200 }}
          />
        )}
      </Card>

      <Card title="提示">
        <p>
          {featureProfile?.feature_ai
            ? 'AI 配置已移至「AI 助手」页面。'
            : 'AI 助手入口默认隐藏，可通过环境变量开启后重启服务。'}
        </p>
      </Card>
    </div>
  );
}
