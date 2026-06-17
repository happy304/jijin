import { useState, useCallback } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import {
  Typography,
  Card,
  Form,
  Select,
  InputNumber,
  Input,
  Button,
  Space,
  Divider,
  message,
  Modal,
  Row,
  Col,
  Tag,
  Table,
  Empty,
  DatePicker,
  Alert,
  Tooltip,
} from 'antd';
import {
  SaveOutlined,
  EditOutlined,
  DeleteOutlined,
  PlusOutlined,
  ThunderboltOutlined,
  ExperimentOutlined,
} from '@ant-design/icons';
import dayjs, { type Dayjs } from 'dayjs';
import {
  STRATEGY_TEMPLATES,
  useStrategyList,
  useCreateStrategy,
  useDeleteStrategy,
  useStrategyDateRange,
  type StrategyType,
  type StrategyCreateRequest,
  type StrategySummary,
  type ParamField,
} from '@/api/strategies';
import { useSubmitBacktest } from '@/api/backtests';
import { useSubmitSimulation, SIMULATION_METHODS, type SimulationMethod } from '@/api/simulations';
import { NLStrategyGen } from '@/components/NLStrategyGen';
import { BacktestDataQualityGate } from './components/BacktestDataQualityGate';
import { useFeatureProfile } from '@/api/settings';
import type { StrategyGenResponse } from '@/api/ai';

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

const PERSONAL_RESEARCH_NOTICE = '策略、回测和模拟仅用于个人研究假设验证，不构成投资建议或交易指令。历史样本、参数和数据质量会显著影响结果，请谨慎解读。';

function extractUniverseCodes(universe: StrategySummary['universe']): string[] {
  if (Array.isArray(universe)) return universe;
  if (universe && typeof universe === 'object' && 'fund_codes' in (universe as Record<string, unknown>)) {
    return ((universe as Record<string, string[]>).fund_codes || []);
  }
  return [];
}

export function StrategiesPage() {
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const [backtestForm] = Form.useForm();
  const [selectedType, setSelectedType] = useState<StrategyType | null>(null);
  const [fundCodes, setFundCodes] = useState<string[]>([]);
  const [showCreateForm, setShowCreateForm] = useState(false);

  // 快速回测弹窗状态
  const [backtestModalOpen, setBacktestModalOpen] = useState(false);
  const [backtestTarget, setBacktestTarget] = useState<StrategySummary | null>(null);

  // 模拟预测弹窗状态
  const [simModalOpen, setSimModalOpen] = useState(false);
  const [simTarget, setSimTarget] = useState<StrategySummary | null>(null);

  const backtestDateRangeValue = Form.useWatch('date_range', backtestForm) as [Dayjs, Dayjs] | undefined;
  const backtestInitialCapital = Form.useWatch('initial_capital', backtestForm) as number | undefined;

  const { data: strategyListData, isLoading: listLoading } = useStrategyList();
  const { data: featureProfile } = useFeatureProfile();
  const createStrategy = useCreateStrategy();
  const deleteStrategy = useDeleteStrategy();
  const submitBacktest = useSubmitBacktest();
  const submitSimulation = useSubmitSimulation();
  const aiEnabled = featureProfile?.feature_ai === true;
  const researchModeEnabled = featureProfile?.schedule_mode === 'research' || featureProfile?.schedule_mode === 'full';

  // 获取当前回测目标策略的可用日期范围
  const { data: backtestDateRange } = useStrategyDateRange(backtestTarget?.id ?? null);

  const selectedTemplate = STRATEGY_TEMPLATES.find((t) => t.type === selectedType);

  // 快速回测：打开弹窗
  const handleQuickBacktest = useCallback((record: StrategySummary) => {
    setBacktestTarget(record);
    setBacktestModalOpen(true);
  }, []);

  // 模拟预测：打开弹窗
  const handleQuickSimulation = useCallback((record: StrategySummary) => {
    setSimTarget(record);
    setSimModalOpen(true);
  }, []);

  // 快速回测：提交
  const handleBacktestSubmit = useCallback(async (values: Record<string, unknown>) => {
    if (!backtestTarget) return;
    const [start, end] = values.date_range as [Dayjs, Dayjs];
    const capital = values.initial_capital as number;
    try {
      const result = await submitBacktest.mutateAsync({
        strategy_id: backtestTarget.id,
        start_date: start.format('YYYY-MM-DD'),
        end_date: end.format('YYYY-MM-DD'),
        initial_capital: capital,
      });
      message.success('回测已提交');
      setBacktestModalOpen(false);
      setBacktestTarget(null);
      navigate(`/backtests/${result.run_id}`);
    } catch {
      // API error handled by interceptor
    }
  }, [backtestTarget, submitBacktest, navigate]);

  // 模拟预测：提交
  const handleSimulationSubmit = useCallback(async (values: Record<string, unknown>) => {
    if (!simTarget) return;
    try {
      const rawTarget = values.target_return as number | null;
      const result = await submitSimulation.mutateAsync({
        strategy_id: simTarget.id,
        horizon_days: values.horizon_days as number,
        num_simulations: values.num_simulations as number,
        method: values.method as SimulationMethod,
        initial_capital: values.initial_capital as number,
        target_return: rawTarget != null ? rawTarget / 100 : null,
      });
      message.success('模拟预测已提交');
      setSimModalOpen(false);
      setSimTarget(null);
      navigate(`/simulations/${result.run_id}`);
    } catch {
      // API error handled by interceptor
    }
  }, [simTarget, submitSimulation, navigate]);

  const handleTypeChange = useCallback(
    (value: StrategyType) => {
      setSelectedType(value);
      // Reset param fields when type changes
      const template = STRATEGY_TEMPLATES.find((t) => t.type === value);
      if (template) {
        const defaults: Record<string, unknown> = {};
        template.params.forEach((p) => {
          defaults[`param_${p.key}`] = p.default;
        });
        form.setFieldsValue(defaults);
      }
    },
    [form],
  );

  const handleSaveStrategy = useCallback(async () => {
    try {
      const values = await form.validateFields([
        'strategy_name',
        'benchmark',
        ...(selectedTemplate?.params.map((p) => `param_${p.key}`) ?? []),
      ]);

      if (fundCodes.length === 0) {
        message.warning('请至少添加一只基金到基金池');
        return;
      }

      const params: Record<string, unknown> = {};
      selectedTemplate?.params.forEach((p) => {
        const val = values[`param_${p.key}`];
        if (val !== undefined && val !== null && val !== '') {
          // Parse JSON string fields that backend expects as objects
          if (p.type === 'string' && typeof val === 'string' && val.startsWith('{')) {
            try {
              params[p.key] = JSON.parse(val);
            } catch {
              params[p.key] = val;
            }
          } else {
            params[p.key] = val;
          }
        }
      });

      const payload: StrategyCreateRequest = {
        name: values.strategy_name,
        strategy_type: selectedType!,
        params,
        universe: fundCodes,
        benchmark: values.benchmark || undefined,
      };

      const result = await createStrategy.mutateAsync(payload);
      message.success('策略保存成功，跳转到策略详情');
      navigate(`/strategies/${result.id}`);
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) {
        // Form validation error - antd handles display
        return;
      }
      // API error is handled by interceptor
    }
  }, [form, selectedTemplate, selectedType, fundCodes, createStrategy, navigate]);

  const renderParamField = (field: ParamField) => {
    const fieldName = `param_${field.key}`;

    if (field.type === 'select' && field.options) {
      return (
        <Form.Item
          key={field.key}
          name={fieldName}
          label={field.label}
          tooltip={field.description}
          rules={field.required ? [{ required: true, message: `请选择${field.label}` }] : undefined}
          initialValue={field.default}
        >
          <Select>
            {field.options.map((opt) => (
              <Select.Option key={opt.value} value={opt.value}>
                {opt.label}
              </Select.Option>
            ))}
          </Select>
        </Form.Item>
      );
    }

    if (field.type === 'number') {
      return (
        <Form.Item
          key={field.key}
          name={fieldName}
          label={field.label}
          tooltip={field.description}
          rules={field.required ? [{ required: true, message: `请输入${field.label}` }] : undefined}
          initialValue={field.default}
        >
          <InputNumber
            min={field.min}
            max={field.max}
            step={field.step ?? 1}
            style={{ width: '100%' }}
          />
        </Form.Item>
      );
    }

    // string type
    return (
      <Form.Item
        key={field.key}
        name={fieldName}
        label={field.label}
        tooltip={field.description}
        rules={field.required ? [{ required: true, message: `请输入${field.label}` }] : undefined}
        initialValue={field.default}
      >
        <Input />
      </Form.Item>
    );
  };

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Title level={3} style={{ margin: 0 }}>策略配置与回测</Title>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setShowCreateForm(!showCreateForm)}
        >
          {showCreateForm ? '收起' : '新建策略'}
        </Button>
      </div>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="研究层功能"
        description={PERSONAL_RESEARCH_NOTICE}
      />

      {!researchModeEnabled && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="当前为轻量调度模式"
          description="策略研究入口仍可手动使用，但系统默认不会执行完整研究层调度任务。需要自动截面评分、策略信号或 IC 验证时，可在后端配置 SCHEDULE_MODE=research/full 后重启服务。"
        />
      )}

      {/* Saved Strategies List */}
      <Card
        title="已保存策略"
        style={{ marginBottom: 16 }}
      >
        <Table<StrategySummary>
          dataSource={strategyListData?.items ?? []}
          loading={listLoading}
          rowKey="id"
          pagination={false}
          locale={{ emptyText: <Empty description="暂无已保存的策略" /> }}
          columns={[
            {
              title: '策略名称',
              dataIndex: 'name',
              key: 'name',
              render: (name: string, record: StrategySummary) => (
                <Link to={`/strategies/${record.id}`}>{name}</Link>
              ),
            },
            {
              title: '策略类型',
              dataIndex: 'strategy_type',
              key: 'strategy_type',
              render: (type: StrategyType) => {
                const template = STRATEGY_TEMPLATES.find((t) => t.type === type);
                return <Tag color="blue">{template?.label ?? type}</Tag>;
              },
            },
            {
              title: '基金数量',
              key: 'fund_count',
              render: (_: unknown, record: StrategySummary) => {
                return extractUniverseCodes(record.universe).length || '-';
              },
            },
            {
              title: '创建时间',
              dataIndex: 'created_at',
              key: 'created_at',
              render: (val: string) => val ? dayjs(val).format('YYYY-MM-DD HH:mm') : '-',
            },
            {
              title: '操作',
              key: 'actions',
              render: (_: unknown, record: StrategySummary) => (
                <Space>
                  <Button
                    type="link"
                    size="small"
                    icon={<ThunderboltOutlined />}
                    onClick={() => handleQuickBacktest(record)}
                    style={{ color: '#52c41a' }}
                  >
                    回测
                  </Button>
                  <Tooltip title={researchModeEnabled ? '用于情景压力测试，不代表未来预测' : '轻量模式默认不推荐使用模拟预测；如需高频研究请启用 research/full 调度模式'}>
                    <Button
                      type="link"
                      size="small"
                      icon={<ExperimentOutlined />}
                      onClick={() => handleQuickSimulation(record)}
                      style={{ color: researchModeEnabled ? '#722ed1' : '#8c8c8c' }}
                    >
                      模拟
                    </Button>
                  </Tooltip>
                  <Button
                    type="link"
                    size="small"
                    icon={<EditOutlined />}
                    onClick={() => navigate(`/strategies/${record.id}`)}
                  >
                    编辑
                  </Button>
                  <Button
                    type="link"
                    size="small"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => {
                      Modal.confirm({
                        title: '确认删除',
                        content: '删除后无法恢复，确定要删除此策略吗？',
                        okText: '确认删除',
                        cancelText: '取消',
                        okButtonProps: { danger: true },
                        onOk: async () => {
                          await deleteStrategy.mutateAsync(record.id);
                          message.success('策略已删除');
                        },
                      });
                    }}
                  >
                    删除
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      {/* Create Strategy Form (collapsible) */}
      {showCreateForm && (
        <>

      {/* Natural Language Strategy Generation Entry */}
      {aiEnabled ? (
        <NLStrategyGen
          onConfirm={(config: StrategyGenResponse) => {
            // Apply the generated config to the form
            const strategyType = config.strategy_type as StrategyType;
            setSelectedType(strategyType);
            form.setFieldsValue({
              strategy_type: strategyType,
              strategy_name: config.name,
            });
            // Set params
            const template = STRATEGY_TEMPLATES.find((t) => t.type === strategyType);
            if (template) {
              template.params.forEach((p) => {
                const val = (config.params as Record<string, unknown>)[p.key];
                if (val !== undefined) {
                  form.setFieldValue(`param_${p.key}`, val);
                }
              });
            }
            // Set fund codes from universe
            const universe = config.universe as Record<string, unknown>;
            if (Array.isArray(universe?.fund_codes)) {
              setFundCodes(universe.fund_codes as string[]);
            }
            message.success('已应用 AI 生成的策略配置，请确认后保存');
          }}
        />
      ) : (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="AI 策略生成已关闭"
          description="个人默认模式下不展示 AI 策略生成入口，可继续手动配置策略模板和基金池。"
        />
      )}

      <Form form={form} layout="vertical" size="large">
        {/* Strategy Template Selection */}
        <Card title="策略模板" style={{ marginBottom: 16 }}>
          <Form.Item
            name="strategy_type"
            label="选择策略类型"
            rules={[{ required: true, message: '请选择策略类型' }]}
          >
            <Select
              placeholder="选择一个策略模板"
              onChange={handleTypeChange}
              options={STRATEGY_TEMPLATES.map((t) => ({
                value: t.type,
                label: (
                  <span>
                    {t.label} <Text type="secondary" style={{ fontSize: 12 }}>- {t.description}</Text>
                  </span>
                ),
              }))}
            />
          </Form.Item>

          <Form.Item
            name="strategy_name"
            label="策略名称"
            rules={[{ required: true, message: '请输入策略名称' }]}
          >
            <Input placeholder="为策略起一个名称" />
          </Form.Item>
        </Card>

        {/* Dynamic Parameter Form */}
        {selectedTemplate && (
          <Card title="策略参数" style={{ marginBottom: 16 }}>
            <Row gutter={16}>
              {selectedTemplate.params.map((field) => (
                <Col span={12} key={field.key}>
                  {renderParamField(field)}
                </Col>
              ))}
            </Row>
          </Card>
        )}

        {/* Fund Pool Selector */}
        <Card title="基金池" style={{ marginBottom: 16 }}>
          <Form.Item label="基金代码" help="输入基金代码后按回车添加，支持多只基金">
            <Select
              mode="tags"
              placeholder="输入基金代码，如 000001、110011"
              value={fundCodes}
              onChange={setFundCodes}
              tokenSeparators={[',', '，', ' ']}
              style={{ width: '100%' }}
            />
          </Form.Item>
          {fundCodes.length > 0 && (
            <div>
              <Text type="secondary">已选 {fundCodes.length} 只基金：</Text>
              <div style={{ marginTop: 8 }}>
                {fundCodes.map((code) => (
                  <Tag
                    key={code}
                    closable
                    onClose={() => setFundCodes(fundCodes.filter((c) => c !== code))}
                  >
                    {code}
                  </Tag>
                ))}
              </div>
            </div>
          )}

          <Divider />

          <Form.Item
            name="benchmark"
            label="基准指数"
            help="可选，如 000300（沪深300）"
          >
            <Input placeholder="输入基准指数代码" />
          </Form.Item>
        </Card>

        {/* Save Strategy */}
        <Card style={{ marginBottom: 16 }}>
          <Button
            type="primary"
            icon={<SaveOutlined />}
            onClick={handleSaveStrategy}
            loading={createStrategy.isPending}
            disabled={!selectedType}
          >
            保存策略
          </Button>
        </Card>
      </Form>
      </>
      )}

      {/* 快速回测弹窗 */}
      <Modal
        title={`快速回测 — ${backtestTarget?.name ?? ''}`}
        open={backtestModalOpen}
        onCancel={() => { setBacktestModalOpen(false); setBacktestTarget(null); }}
        footer={null}
        destroyOnClose
        width={720}
      >
        <BacktestDataQualityGate
          strategyId={backtestTarget?.id}
          startDate={backtestDateRangeValue?.[0]?.format('YYYY-MM-DD')}
          endDate={backtestDateRangeValue?.[1]?.format('YYYY-MM-DD')}
          initialCapital={backtestInitialCapital}
          fundCount={backtestTarget ? extractUniverseCodes(backtestTarget.universe).length : undefined}
          enabled={backtestModalOpen}
        />
        <Form
          form={backtestForm}
          layout="vertical"
          onFinish={handleBacktestSubmit}
          initialValues={{
            initial_capital: 100000,
            date_range: [
              backtestDateRange?.earliest_date
                ? dayjs(backtestDateRange.earliest_date)
                : dayjs().subtract(3, 'year'),
              dayjs(),
            ],
          }}
          key={`${backtestTarget?.id}-${backtestDateRange?.earliest_date ?? ''}`}
        >
          <Form.Item
            name="date_range"
            label="回测区间"
            rules={[{ required: true, message: '请选择回测区间' }]}
            help={
              backtestDateRange?.earliest_date
                ? `基金池最早可用数据: ${backtestDateRange.earliest_date}`
                : undefined
            }
          >
            <RangePicker
              style={{ width: '100%' }}
              disabledDate={(current) => {
                if (!current) return false;
                if (current.isAfter(dayjs(), 'day')) return true;
                if (backtestDateRange?.earliest_date && current.isBefore(dayjs(backtestDateRange.earliest_date), 'day')) {
                  return true;
                }
                return false;
              }}
              presets={[
                { label: '近1年', value: [dayjs().subtract(1, 'year'), dayjs()] },
                { label: '近2年', value: [dayjs().subtract(2, 'year'), dayjs()] },
                { label: '近3年', value: [dayjs().subtract(3, 'year'), dayjs()] },
                { label: '近5年', value: [dayjs().subtract(5, 'year'), dayjs()] },
              ]}
            />
          </Form.Item>
          <Form.Item
            name="initial_capital"
            label="初始资金（元）"
            rules={[{ required: true, message: '请输入初始资金' }]}
          >
            <InputNumber
              min={1000}
              step={10000}
              style={{ width: '100%' }}
              formatter={(value) => `${value}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
              parser={(value) => (Number(value?.replace(/,/g, '') || '0')) as 1000}
            />
          </Form.Item>
          <Form.Item style={{ marginBottom: 0, textAlign: 'right' }}>
            <Space>
              <Button onClick={() => { setBacktestModalOpen(false); setBacktestTarget(null); }}>
                取消
              </Button>
              <Button
                type="primary"
                htmlType="submit"
                icon={<ThunderboltOutlined />}
                loading={submitBacktest.isPending}
              >
                开始回测
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 模拟预测弹窗 */}
      <Modal
        title={`模拟预测 — ${simTarget?.name ?? ''}`}
        open={simModalOpen}
        onCancel={() => { setSimModalOpen(false); setSimTarget(null); }}
        footer={null}
        destroyOnClose
        width={520}
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="模拟预测仅作情景观察"
          description="该功能基于历史样本和分布假设生成多路径情景，不代表未来收益、目标达成概率或交易建议。"
        />
        {simTarget && (
          <div style={{ marginBottom: 16, padding: '8px 12px', background: '#f6f8fa', borderRadius: 6 }}>
            <span style={{ color: '#666' }}>策略类型：</span>
            <Tag color="blue">
              {STRATEGY_TEMPLATES.find((t) => t.type === simTarget.strategy_type)?.label ?? simTarget.strategy_type}
            </Tag>
            <span style={{ color: '#999', fontSize: 12, marginLeft: 8 }}>
              {STRATEGY_TEMPLATES.find((t) => t.type === simTarget.strategy_type)?.description ?? ''}
            </span>
          </div>
        )}
        <Form
          layout="vertical"
          onFinish={handleSimulationSubmit}
          initialValues={{
            method: 'gbm',
            horizon_days: 252,
            num_simulations: 10000,
            initial_capital: 100000,
            target_return: null,
          }}
          key={simTarget?.id}
        >
          <Form.Item
            name="method"
            label="模拟方法"
            rules={[{ required: true, message: '请选择模拟方法' }]}
          >
            <Select>
              {SIMULATION_METHODS.map((m) => (
                <Select.Option key={m.value} value={m.value}>
                  {m.label}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="horizon_days"
                label="预测期限（交易日）"
                rules={[{ required: true, message: '请输入预测期限' }]}
              >
                <InputNumber min={20} max={1260} step={21} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="num_simulations"
                label="模拟路径数"
                rules={[{ required: true, message: '请输入模拟次数' }]}
              >
                <InputNumber min={1000} max={100000} step={1000} style={{ width: '100%' }} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="initial_capital"
                label="初始资金（元）"
                rules={[{ required: true, message: '请输入初始资金' }]}
              >
                <InputNumber
                  min={1000}
                  step={10000}
                  style={{ width: '100%' }}
                  formatter={(value) => `${value}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
                  parser={(value) => (Number(value?.replace(/,/g, '') || '0')) as 1000}
                />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="target_return"
                label="目标收益率（%）"
                help="可选，如输入 10 表示 10%"
              >
                <InputNumber
                  min={-100}
                  max={1000}
                  step={5}
                  style={{ width: '100%' }}
                  placeholder="留空则不设目标"
                  suffix="%"
                />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item style={{ marginBottom: 0, textAlign: 'right' }}>
            <Space>
              <Button onClick={() => { setSimModalOpen(false); setSimTarget(null); }}>
                取消
              </Button>
              <Button
                type="primary"
                htmlType="submit"
                icon={<ExperimentOutlined />}
                loading={submitSimulation.isPending}
              >
                开始模拟
              </Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
