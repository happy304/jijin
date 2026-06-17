import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Typography,
  Card,
  Form,
  Select,
  InputNumber,
  Input,
  Button,
  Space,
  DatePicker,
  Divider,
  message,
  Modal,
  Row,
  Col,
  Tag,
  Spin,
  Result,
} from 'antd';
import {
  SaveOutlined,
  ArrowLeftOutlined,
  DeleteOutlined,
  RocketOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import dayjs, { Dayjs } from 'dayjs';
import {
  STRATEGY_TEMPLATES,
  useStrategy,
  useUpdateStrategy,
  useDeleteStrategy,
  useStrategyDateRange,
  type StrategyType,
  type StrategyUpdateRequest,
  type ParamField,
} from '@/api/strategies';
import { useSubmitBacktest } from '@/api/backtests';
import { useSubmitSimulation } from '@/api/simulations';
import { BacktestDataQualityGate } from './components/BacktestDataQualityGate';

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

export function StrategyDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [form] = Form.useForm();
  const [selectedType, setSelectedType] = useState<StrategyType | null>(null);
  const [fundCodes, setFundCodes] = useState<string[]>([]);

  const strategyId = id ? parseInt(id, 10) : null;
  const { data: strategy, isLoading, error } = useStrategy(strategyId);
  const { data: dateRange } = useStrategyDateRange(strategyId);
  const updateStrategy = useUpdateStrategy();
  const deleteStrategy = useDeleteStrategy();
  const submitBacktest = useSubmitBacktest();
  const submitSimulation = useSubmitSimulation();
  const backtestDateRange = Form.useWatch('date_range', form) as [Dayjs, Dayjs] | undefined;
  const backtestInitialCapital = Form.useWatch('initial_capital', form) as number | undefined;

  const selectedTemplate = STRATEGY_TEMPLATES.find((t) => t.type === selectedType);

  // Populate form when strategy data loads
  useEffect(() => {
    if (strategy) {
      setSelectedType(strategy.strategy_type);

      // Extract fund codes from universe
      const universe = strategy.universe as unknown;
      if (Array.isArray(universe)) {
        setFundCodes(universe as string[]);
      } else if (universe && typeof universe === 'object' && 'fund_codes' in (universe as Record<string, unknown>)) {
        setFundCodes((universe as Record<string, string[]>).fund_codes || []);
      }

      // Set form values
      const formValues: Record<string, unknown> = {
        strategy_name: strategy.name,
        strategy_type: strategy.strategy_type,
        benchmark: strategy.benchmark,
      };

      // Set param fields
      const template = STRATEGY_TEMPLATES.find((t) => t.type === strategy.strategy_type);
      if (template && strategy.params) {
        template.params.forEach((p) => {
          const val = strategy.params[p.key];
          if (val !== undefined) {
            formValues[`param_${p.key}`] = val;
          }
        });
      }

      form.setFieldsValue(formValues);
    }
  }, [strategy, form]);

  // Update date_range when dateRange loads
  useEffect(() => {
    if (dateRange?.earliest_date) {
      const currentRange = form.getFieldValue('date_range');
      const earliest = dayjs(dateRange.earliest_date);
      // If current start is before earliest available, adjust it
      if (currentRange && currentRange[0] && currentRange[0].isBefore(earliest, 'day')) {
        form.setFieldsValue({
          date_range: [earliest, currentRange[1] || dayjs()],
        });
      }
    }
  }, [dateRange, form]);

  const handleTypeChange = useCallback(
    (value: StrategyType) => {
      setSelectedType(value);
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

  const handleSave = useCallback(async () => {
    if (!strategyId) return;

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
          params[p.key] = val;
        }
      });

      const payload: StrategyUpdateRequest & { id: number } = {
        id: strategyId,
        name: values.strategy_name,
        strategy_type: selectedType!,
        params,
        universe: fundCodes,
        benchmark: values.benchmark || undefined,
      };

      await updateStrategy.mutateAsync(payload);
      message.success('策略更新成功');
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) {
        return;
      }
    }
  }, [form, selectedTemplate, selectedType, fundCodes, strategyId, updateStrategy]);

  const handleDelete = useCallback(async () => {
    if (!strategyId) return;
    Modal.confirm({
      title: '确认删除',
      content: '删除后无法恢复，确定要删除此策略吗？',
      okText: '确认删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: async () => {
        await deleteStrategy.mutateAsync(strategyId);
        message.success('策略已删除');
        navigate('/strategies');
      },
    });
  }, [strategyId, deleteStrategy, navigate]);

  const handleSubmitBacktest = useCallback(async () => {
    if (!strategyId) return;

    try {
      const values = await form.validateFields(['date_range', 'initial_capital']);
      const [start, end] = values.date_range as [Dayjs, Dayjs];

      const result = await submitBacktest.mutateAsync({
        strategy_id: strategyId,
        start_date: start.format('YYYY-MM-DD'),
        end_date: end.format('YYYY-MM-DD'),
        initial_capital: values.initial_capital,
      });

      message.success('回测已提交');
      navigate(`/backtests/${result.run_id}`);
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) {
        return;
      }
      // API 错误提示
      const detail = (err as any)?.response?.data?.detail || (err as Error)?.message || '提交回测失败';
      message.error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
  }, [form, strategyId, submitBacktest, navigate]);

  const handleSubmitSimulation = useCallback(async () => {
    if (!strategyId) return;

    try {
      const values = await form.validateFields(['initial_capital']);

      const result = await submitSimulation.mutateAsync({
        strategy_id: strategyId,
        initial_capital: values.initial_capital,
        horizon_days: 252,
        num_simulations: 10000,
        method: 'gbm',
      });

      message.success('模拟预测已提交');
      navigate(`/simulations/${result.run_id}`);
    } catch (err) {
      if (err && typeof err === 'object' && 'errorFields' in err) {
        return;
      }
      // API 错误提示
      const detail = (err as any)?.response?.data?.detail || (err as Error)?.message || '提交模拟失败';
      message.error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
  }, [form, strategyId, submitSimulation, navigate]);

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

    return (
      <Form.Item
        key={field.key}
        name={fieldName}
        label={field.label}
        tooltip={field.description}
        rules={field.required ? [{ required: true, message: `请输入${field.label}` }] : undefined}
      >
        <Input />
      </Form.Item>
    );
  };

  if (isLoading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  if (error || !strategy) {
    return (
      <Result
        status="404"
        title="策略不存在"
        subTitle="该策略可能已被删除"
        extra={
          <Button type="primary" onClick={() => navigate('/strategies')}>
            返回策略列表
          </Button>
        }
      />
    );
  }

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <Space style={{ marginBottom: 16 }}>
        <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/strategies')}>
          返回列表
        </Button>
      </Space>

      <Title level={3}>编辑策略</Title>

      <Form form={form} layout="vertical" size="large">
        {/* Strategy Template Selection */}
        <Card title="策略模板" style={{ marginBottom: 16 }}>
          <Form.Item
            name="strategy_type"
            label="策略类型"
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

        {/* Action Buttons */}
        <Card style={{ marginBottom: 16 }}>
          <Space>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              onClick={handleSave}
              loading={updateStrategy.isPending}
              disabled={!selectedType}
            >
              保存修改
            </Button>
            <Button
              danger
              icon={<DeleteOutlined />}
              onClick={handleDelete}
              loading={deleteStrategy.isPending}
            >
              删除策略
            </Button>
          </Space>
        </Card>

        {/* Backtest Submission */}
        <Card title={<><ThunderboltOutlined /> 提交回测</>} style={{ marginBottom: 16 }}>
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="date_range"
                label="回测区间"
                rules={[{ required: true, message: '请选择回测日期范围' }]}
                initialValue={[
                  dateRange?.earliest_date
                    ? dayjs(dateRange.earliest_date)
                    : dayjs().subtract(3, 'year'),
                  dayjs(),
                ]}
                help={
                  dateRange?.earliest_date
                    ? `基金池最早可用数据: ${dateRange.earliest_date}`
                    : undefined
                }
              >
                <RangePicker
                  style={{ width: '100%' }}
                  disabledDate={(current) => {
                    if (!current) return false;
                    // 不能选未来日期
                    if (current.isAfter(dayjs(), 'day')) return true;
                    // 不能选早于基金池最早可用数据的日期
                    if (dateRange?.earliest_date && current.isBefore(dayjs(dateRange.earliest_date), 'day')) {
                      return true;
                    }
                    return false;
                  }}
                />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="initial_capital"
                label="初始资金"
                rules={[{ required: true, message: '请输入初始资金' }]}
                initialValue={100000}
              >
                <InputNumber
                  min={1000}
                  step={10000}
                  style={{ width: '100%' }}
                  addonAfter="元"
                  formatter={(value) => `${value}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}
                />
              </Form.Item>
            </Col>
          </Row>

          <BacktestDataQualityGate
            strategyId={strategyId}
            startDate={backtestDateRange?.[0]?.format('YYYY-MM-DD')}
            endDate={backtestDateRange?.[1]?.format('YYYY-MM-DD')}
            initialCapital={backtestInitialCapital}
            fundCount={fundCodes.length}
          />

          <Button
            type="primary"
            icon={<RocketOutlined />}
            onClick={handleSubmitBacktest}
            loading={submitBacktest.isPending}
            size="large"
          >
            提交回测
          </Button>
          <Button
            icon={<ThunderboltOutlined />}
            onClick={handleSubmitSimulation}
            loading={submitSimulation.isPending}
            size="large"
            style={{ marginLeft: 12 }}
          >
            模拟预测
          </Button>
        </Card>
      </Form>
    </div>
  );
}
