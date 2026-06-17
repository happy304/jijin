import { useState } from 'react';
import { Alert, Button, Card, Col, Form, Input, InputNumber, List, message, Modal, Popconfirm, Row, Select, Space, Statistic, Table, Tag, Typography, Upload } from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { useQueryClient } from '@tanstack/react-query';
import {
  useCreateAdvisorExecutionRecord,
  useDeleteAdvisorExecutionRecord,
  useImportAdvisorExecutionRecords,
  useUpdateAdvisorExecutionRecord,
  type AdvisorExecutionRecord,
  type AdvisorExecutionRecordRequest,
  type AdvisorExecutionSummary,
  type AdvisorHistoryDetailResponse,
  type ExecutionPlanTaskItem as ApiExecutionPlanTaskItem,
  type ExecutionStatus,
  type TradingAdviceItem,
} from '@/api/advisor';
import {
  driftLevelColor,
  driftLevelLabel,
  executionStatusColor,
  executionStatusLabel,
  executionSummaryStatusLabel,
  formatCurrency,
  formatDateWithWeekday,
} from '@/utils/advisorDisplay';

const { Text } = Typography;

const ACTION_CONFIG = {
  buy: { text: '可关注增配', tagColor: 'red' },
  sell: { text: '可关注减配', tagColor: 'green' },
  hold: { text: '继续观察', tagColor: 'default' },
  watch: { text: '观察', tagColor: 'blue' },
};

export type ExecutionPlanTaskItem = ApiExecutionPlanTaskItem & { key: string };

export interface ExecutionPlanTaskContext {
  task: ExecutionPlanTaskItem;
  source: 'execution_plan';
}

interface ExecutionRecordFormValues {
  fund_code: string;
  execution_status: ExecutionStatus;
  executed_date?: string;
  executed_amount?: number;
  executed_shares?: number;
  executed_nav?: number;
  executed_fee?: number;
  execution_channel?: string;
  not_executed_reason?: string;
  deviation_reason?: string;
  user_note?: string;
}

function normalizeExecutionPlanTask(task: ApiExecutionPlanTaskItem): ExecutionPlanTaskItem {
  return {
    ...task,
    key: task.task_key,
  };
}

export function getExecutionPlanTasks(advice: TradingAdviceItem, detail?: AdvisorHistoryDetailResponse | null): ExecutionPlanTaskItem[] {
  const fundTasks = detail?.execution_plan_status?.by_fund?.[advice.fund_code]?.tasks || [];
  return fundTasks.map(normalizeExecutionPlanTask);
}

export function getPendingExecutionPlanTasks(advice: TradingAdviceItem, detail?: AdvisorHistoryDetailResponse | null): ExecutionPlanTaskItem[] {
  return getExecutionPlanTasks(advice, detail).filter((task) => task.status === 'pending');
}

function ExecutionSummaryStrip({ summary }: { summary?: AdvisorExecutionSummary | null }) {
  if (!summary) {
    return (
      <Alert
        type="info"
        showIcon
        message="尚未记录实际执行"
        description="记录执行后，系统会在复盘中区分模型参考结果表现与用户实际采纳/偏离情况。"
        style={{ marginBottom: 12 }}
      />
    );
  }

  return (
    <>
      <Row gutter={16} style={{ marginBottom: 12 }}>
        <Col span={6}>
          <Statistic title="执行记录" value={summary.record_count} suffix="条" />
        </Col>
        <Col span={6}>
          <Statistic
            title="可执行参考采纳率"
            value={summary.adoption_rate != null ? `${(summary.adoption_rate * 100).toFixed(0)}%` : '-'}
            suffix={summary.actionable_advice_count > 0 ? `(${summary.adopted_count}/${summary.actionable_advice_count})` : ''}
          />
        </Col>
        <Col span={6}>
          <Statistic
            title="平均金额偏离"
            value={summary.avg_abs_amount_deviation_pct != null ? `${(summary.avg_abs_amount_deviation_pct * 100).toFixed(1)}%` : '-'}
          />
        </Col>
        <Col span={6}>
          <Statistic title="大幅偏离" value={summary.significant_deviation_count} suffix="只" />
        </Col>
      </Row>
      <Alert
        type={summary.status === 'fully_adopted' ? 'success' : summary.status === 'no_execution_records' ? 'info' : summary.status === 'not_adopted' ? 'warning' : 'info'}
        showIcon
        message={`执行归因：${executionSummaryStatusLabel(summary.status)}`}
        description={summary.interpretation}
        style={{ marginBottom: 12 }}
      />
    </>
  );
}

export function AdvisorExecutionRecordsCard({ detail }: { detail: AdvisorHistoryDetailResponse }) {
  const [form] = Form.useForm<ExecutionRecordFormValues>();
  const queryClient = useQueryClient();
  const createMutation = useCreateAdvisorExecutionRecord();
  const updateMutation = useUpdateAdvisorExecutionRecord();
  const deleteMutation = useDeleteAdvisorExecutionRecord();
  const importMutation = useImportAdvisorExecutionRecords();
  const [editingRecord, setEditingRecord] = useState<AdvisorExecutionRecord | null>(null);
  const [selectedAdvice, setSelectedAdvice] = useState<TradingAdviceItem | null>(null);
  const [selectedTaskContext, setSelectedTaskContext] = useState<ExecutionPlanTaskContext | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const executionStatus = Form.useWatch('execution_status', form) as ExecutionStatus | undefined;

  const refreshRelatedQueries = () => {
    queryClient.invalidateQueries({ queryKey: ['advisor-history-detail', detail.id] });
    queryClient.invalidateQueries({ queryKey: ['advisor-performance', detail.id] });
    queryClient.invalidateQueries({ queryKey: ['advisor-history'] });
    queryClient.invalidateQueries({ queryKey: ['advisor-reminders'] });
  };

  const openCreateModal = (advice: TradingAdviceItem, task?: ExecutionPlanTaskItem) => {
    setEditingRecord(null);
    setSelectedAdvice(advice);
    setSelectedTaskContext(task ? { task, source: 'execution_plan' } : null);
    const defaultStatus: ExecutionStatus = advice.action === 'hold' ? 'planned' : 'executed';
    form.setFieldsValue({
      fund_code: advice.fund_code,
      execution_status: task?.status === 'skipped' ? 'not_executed' : defaultStatus,
      executed_date: task?.scheduled_date || detail.advice_date,
      executed_amount: task?.amount_max ?? (advice.suggested_amount > 0 ? advice.suggested_amount : undefined),
      executed_shares: advice.suggested_shares ?? undefined,
      executed_fee: advice.fee_estimate?.estimated_fee ?? undefined,
      execution_channel: undefined,
      not_executed_reason: task?.status === 'skipped' ? '按计划任务跳过' : undefined,
      deviation_reason: task ? `按计划任务执行：${task.title}` : undefined,
      user_note: task ? `来源任务：${task.title}` : undefined,
    });
    setModalOpen(true);
  };

  const openEditModal = (record: AdvisorExecutionRecord) => {
    setEditingRecord(record);
    setSelectedTaskContext(null);
    setSelectedAdvice(detail.advices.find((advice) => advice.fund_code === record.fund_code) || null);
    form.setFieldsValue({
      fund_code: record.fund_code,
      execution_status: record.execution_status,
      executed_date: record.executed_date || undefined,
      executed_amount: record.executed_amount ?? undefined,
      executed_shares: record.executed_shares ?? undefined,
      executed_nav: record.executed_nav ?? undefined,
      executed_fee: record.executed_fee ?? undefined,
      execution_channel: record.execution_channel || undefined,
      not_executed_reason: record.not_executed_reason || undefined,
      deviation_reason: record.deviation_reason || undefined,
      user_note: record.user_note || undefined,
    });
    setModalOpen(true);
  };

  const closeModal = () => {
    setModalOpen(false);
    setEditingRecord(null);
    setSelectedAdvice(null);
    setSelectedTaskContext(null);
    form.resetFields();
  };

  const normalizeRecordPayload = (values: ExecutionRecordFormValues): AdvisorExecutionRecordRequest => ({
    fund_code: values.fund_code,
    execution_status: values.execution_status,
    advice_action: selectedAdvice?.action || editingRecord?.advice_action || null,
    trade_intent: selectedAdvice?.trade_intent || editingRecord?.trade_intent || null,
    executed_date: values.executed_date || null,
    executed_amount: values.executed_amount ?? null,
    executed_shares: values.executed_shares ?? null,
    executed_nav: values.executed_nav ?? null,
    executed_fee: values.executed_fee ?? null,
    execution_channel: values.execution_channel || null,
    not_executed_reason: values.not_executed_reason || null,
    deviation_reason: values.deviation_reason || null,
    user_note: values.user_note || null,
    source: 'manual',
    metadata: selectedTaskContext ? {
      execution_plan_task_key: selectedTaskContext.task.key,
      execution_plan_task_title: selectedTaskContext.task.title,
      execution_plan_scheduled_date: selectedTaskContext.task.scheduled_date,
      execution_plan_amount_min: selectedTaskContext.task.amount_min ?? null,
      execution_plan_amount_max: selectedTaskContext.task.amount_max ?? null,
      execution_plan_source: selectedTaskContext.source,
    } : null,
  });

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();
      if (values.execution_status === 'not_executed' && !values.not_executed_reason) {
        message.warning('未执行状态需要填写未执行原因');
        return;
      }
      if ((values.execution_status === 'executed' || values.execution_status === 'partial') && !values.executed_date) {
        message.warning('已执行/部分执行需要填写成交日期');
        return;
      }

      const payload = normalizeRecordPayload(values);
      if (editingRecord) {
        const updatePayload = {
          execution_status: payload.execution_status,
          executed_date: payload.executed_date,
          executed_amount: payload.executed_amount,
          executed_shares: payload.executed_shares,
          executed_nav: payload.executed_nav,
          executed_fee: payload.executed_fee,
          execution_channel: payload.execution_channel,
          not_executed_reason: payload.not_executed_reason,
          deviation_reason: payload.deviation_reason,
          user_note: payload.user_note,
          metadata: payload.metadata,
        };
        await updateMutation.mutateAsync({ executionId: editingRecord.id, record: updatePayload });
        message.success('执行记录已更新');
      } else {
        await createMutation.mutateAsync({ resultId: detail.id, record: payload });
        message.success('执行记录已保存');
      }

      refreshRelatedQueries();
      closeModal();
    } catch (err) {
      if (err instanceof Error) return;
      message.error('保存执行记录失败');
    }
  };

  const handleDelete = async (record: AdvisorExecutionRecord) => {
    try {
      await deleteMutation.mutateAsync(record.id);
      message.success('执行记录已删除');
      refreshRelatedQueries();
    } catch {
      message.error('删除执行记录失败');
    }
  };

  const handleImportFile = async (file: File) => {
    try {
      const result = await importMutation.mutateAsync({ resultId: detail.id, file });
      refreshRelatedQueries();
      const failedRows = result.rows.filter((row) => row.status === 'failed');
      if (failedRows.length > 0) {
        Modal.warning({
          title: `导入完成：成功 ${result.created_count} 条，失败 ${result.failed_count} 条`,
          width: 720,
          content: (
            <div>
              <Text type="secondary">请按行号修正失败记录后重新导入。已成功的记录已经保存。</Text>
              <Table
                size="small"
                style={{ marginTop: 12 }}
                rowKey="row_number"
                dataSource={failedRows}
                pagination={false}
                columns={[
                  { title: '行号', dataIndex: 'row_number', width: 80 },
                  { title: '基金', dataIndex: 'fund_code', width: 100, render: (value: string | null) => value || '-' },
                  { title: '失败原因', dataIndex: 'error' },
                ]}
              />
            </div>
          ),
        });
      } else {
        message.success(`成功导入 ${result.created_count} 条执行记录`);
      }
    } catch {
      message.error('导入执行记录失败，请检查文件格式和字段');
    }
    return false;
  };

  const records = detail.execution_records || [];
  const summary = detail.execution_summary;
  const executionPlanStatus = detail.execution_plan_status;
  const recordsByFund = records.reduce<Record<string, AdvisorExecutionRecord[]>>((acc, record) => {
    acc[record.fund_code] = acc[record.fund_code] || [];
    acc[record.fund_code].push(record);
    return acc;
  }, {});

  const adviceRows = detail.advices.map((advice) => ({
    ...advice,
    execution_summary: summary?.by_fund?.[advice.fund_code],
    execution_records: recordsByFund[advice.fund_code] || [],
    plan_tasks: getExecutionPlanTasks(advice, detail),
    pending_plan_tasks: getPendingExecutionPlanTasks(advice, detail),
  }));

  return (
    <Card
      size="small"
      title="用户实际执行记录"
      style={{ marginBottom: 16 }}
      extra={
        <Upload
          accept=".csv,.xlsx,.xls"
          showUploadList={false}
          beforeUpload={handleImportFile}
        >
          <Button size="small" icon={<UploadOutlined />} loading={importMutation.isPending}>
            导入成交记录
          </Button>
        </Upload>
      }
    >
      <Alert
        type="info"
        showIcon
        message="批量导入模板字段"
        description="支持 CSV/Excel：基金代码、执行状态、成交日期、成交金额、成交份额、成交净值、手续费、渠道、未执行原因、偏离原因、备注。执行状态可填：已执行、部分执行、计划执行、未执行。"
        style={{ marginBottom: 12 }}
      />
      <ExecutionSummaryStrip summary={summary} />
      {executionPlanStatus?.summary?.task_count ? (
        <Alert
          type={executionPlanStatus.summary.pending_count > 0 ? 'warning' : 'success'}
          showIcon
          message={`执行计划任务：共 ${executionPlanStatus.summary.task_count} 个，待执行 ${executionPlanStatus.summary.pending_count} 个，已完成 ${executionPlanStatus.summary.done_count} 个，已跳过 ${executionPlanStatus.summary.skipped_count} 个`}
          description={executionPlanStatus.summary.pending_count > 0 ? '待执行任务会根据已记录的执行状态自动推进；补记执行记录后这里会自动刷新。' : '当前计划任务均已处理完毕。'}
          style={{ marginBottom: 12 }}
        />
      ) : null}
      <Table
        size="small"
        rowKey="fund_code"
        dataSource={adviceRows}
        pagination={false}
        scroll={{ x: 1100 }}
        columns={[
          { title: '基金', dataIndex: 'fund_code', width: 100 },
          { title: '原检查结论', dataIndex: 'action', width: 80, render: (action: string) => { const c = ACTION_CONFIG[action as keyof typeof ACTION_CONFIG] || ACTION_CONFIG.hold; return <Tag color={c.tagColor}>{c.text}</Tag>; } },
          { title: '参考调整金额', dataIndex: 'suggested_amount', width: 110, align: 'right' as const, render: (value: number) => formatCurrency(value) },
          { title: '最新执行状态', key: 'latest_status', width: 110, render: (_, row) => <Tag color={executionStatusColor(row.execution_summary?.latest_status)}>{executionStatusLabel(row.execution_summary?.latest_status)}</Tag> },
          { title: '已执行金额', key: 'executed_amount', width: 120, align: 'right' as const, render: (_, row) => formatCurrency(row.execution_summary?.total_executed_amount) },
          { title: '金额执行率', key: 'execution_ratio', width: 110, render: (_, row) => row.execution_summary?.amount_execution_ratio != null ? <Tag color={driftLevelColor(row.execution_summary.drift_level)}>{(row.execution_summary.amount_execution_ratio * 100).toFixed(0)}%</Tag> : '-' },
          { title: '偏离', key: 'drift', width: 110, render: (_, row) => <Tag color={driftLevelColor(row.execution_summary?.drift_level)}>{driftLevelLabel(row.execution_summary?.drift_level)}</Tag> },
          { title: '记录数', key: 'record_count', width: 80, render: (_, row) => row.execution_records.length },
          { title: '计划任务', key: 'pending_tasks', width: 140, render: (_, row) => row.plan_tasks.length > 0 ? <Space size={4} wrap><Tag color="blue">待执行 {row.pending_plan_tasks.length}</Tag><Tag color="green">已完成 {row.plan_tasks.filter((task: ExecutionPlanTaskItem) => task.status === 'done').length}</Tag></Space> : '-' },
          { title: '操作', key: 'actions', width: 240, render: (_, row) => (
            <Space wrap>
              <Button size="small" onClick={() => openCreateModal(row)}>记录执行</Button>
              {row.pending_plan_tasks[0] && <Button size="small" type="primary" ghost onClick={() => openCreateModal(row, row.pending_plan_tasks[0])}>记录下个任务</Button>}
              {row.execution_records[0] && <Button size="small" onClick={() => openEditModal(row.execution_records[0])}>编辑</Button>}
            </Space>
          ) },
        ]}
        expandable={{
          expandedRowRender: (row) => (
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              {row.plan_tasks.length > 0 && (
                <Card size="small" type="inner" title="计划任务" extra={<Text type="secondary">任务状态由执行记录自动推导</Text>}>
                  <List
                    size="small"
                    dataSource={row.plan_tasks}
                    renderItem={(task) => (
                      <List.Item
                        style={{ padding: '8px 0' }}
                        actions={task.status === 'pending' ? [<Button key="record" size="small" type="link" onClick={() => openCreateModal(row, task)}>按此任务记录</Button>] : []}
                      >
                        <Space direction="vertical" size={2} style={{ width: '100%' }}>
                          <Space wrap>
                            <Text strong>{task.title}</Text>
                            <Tag color={task.status === 'done' ? 'green' : task.status === 'skipped' ? 'default' : 'blue'}>
                              {task.status === 'done' ? '已完成' : task.status === 'skipped' ? '已跳过' : '待执行'}
                            </Tag>
                            <Tag>{formatDateWithWeekday(task.scheduled_date)}</Tag>
                            <Tag color="purple">{formatCurrency(task.amount_min)} - {formatCurrency(task.amount_max)}</Tag>
                            {task.matched_execution_status ? <Tag color={executionStatusColor(task.matched_execution_status)}>{executionStatusLabel(task.matched_execution_status)}</Tag> : null}
                          </Space>
                          <Text type="secondary" style={{ fontSize: 12 }}>{task.description}</Text>
                          {task.trigger_summary ? <Text type="secondary" style={{ fontSize: 12 }}>触发说明：{task.trigger_summary}</Text> : null}
                          {task.matched_executed_date ? <Text type="secondary" style={{ fontSize: 12 }}>最近执行日期：{formatDateWithWeekday(task.matched_executed_date)}</Text> : null}
                        </Space>
                      </List.Item>
                    )}
                  />
                </Card>
              )}
              {row.execution_records.length > 0 ? (
                <Table
                  size="small"
                  rowKey="id"
                  dataSource={row.execution_records}
                  pagination={false}
                  columns={[
                    { title: '状态', dataIndex: 'execution_status', width: 100, render: (status: string) => <Tag color={executionStatusColor(status)}>{executionStatusLabel(status)}</Tag> },
                    { title: '成交日期', dataIndex: 'executed_date', width: 110, render: (value: string | null) => value || '-' },
                    { title: '成交金额', dataIndex: 'executed_amount', width: 110, align: 'right' as const, render: (value: number | null) => formatCurrency(value) },
                    { title: '成交份额', dataIndex: 'executed_shares', width: 110, render: (value: number | null) => value != null ? value.toLocaleString() : '-' },
                    { title: '成交净值', dataIndex: 'executed_nav', width: 90, render: (value: number | null) => value != null ? value.toFixed(4) : '-' },
                    { title: '费用', dataIndex: 'executed_fee', width: 90, align: 'right' as const, render: (value: number | null) => formatCurrency(value) },
                    { title: '渠道', dataIndex: 'execution_channel', width: 100, render: (value: string | null) => value || '-' },
                    { title: '任务来源', key: 'task_meta', width: 170, render: (_, record) => record.metadata?.execution_plan_task_title ? <Text type="secondary" style={{ fontSize: 12 }}>{String(record.metadata.execution_plan_task_title)}</Text> : '-' },
                    { title: '原因/备注', key: 'notes', render: (_, record) => [record.not_executed_reason, record.deviation_reason, record.user_note].filter(Boolean).join('；') || '-' },
                    { title: '操作', key: 'actions', width: 130, render: (_, record) => (
                      <Space>
                        <Button size="small" onClick={() => openEditModal(record)}>编辑</Button>
                        <Popconfirm title="确认删除这条执行记录？" onConfirm={() => handleDelete(record)}>
                          <Button size="small" danger loading={deleteMutation.isPending}>删除</Button>
                        </Popconfirm>
                      </Space>
                    ) },
                  ]}
                />
              ) : <Text type="secondary">暂无执行记录，点击“记录执行”或上方“按此任务记录”补充实际操作。</Text>}
            </Space>
          ),
        }}
      />

      <Modal
        title={editingRecord ? '编辑执行记录' : selectedTaskContext ? `记录任务执行 · ${selectedTaskContext.task.title}` : '记录实际执行'}
        open={modalOpen}
        onCancel={closeModal}
        onOk={handleSubmit}
        confirmLoading={createMutation.isPending || updateMutation.isPending}
        destroyOnClose
      >
        <Form form={form} layout="vertical" initialValues={{ execution_status: 'executed' }}>
          {selectedTaskContext && !editingRecord && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message={`本次正在记录任务：${selectedTaskContext.task.title}`}
              description={`计划日期 ${formatDateWithWeekday(selectedTaskContext.task.scheduled_date)}，参考调整金额区间 ${formatCurrency(selectedTaskContext.task.amount_min)} - ${formatCurrency(selectedTaskContext.task.amount_max)}。保存已执行/部分执行后会自动把该任务标记为已完成；保存未执行会自动标记为已跳过。`}
            />
          )}
          <Form.Item name="fund_code" label="基金代码" rules={[{ required: true, message: '请选择基金' }]}>
            <Select disabled={!!editingRecord} options={detail.advices.map((advice) => ({ value: advice.fund_code, label: `${advice.fund_code} - ${advice.fund_name || ''}` }))} />
          </Form.Item>
          <Form.Item name="execution_status" label="执行状态" rules={[{ required: true, message: '请选择执行状态' }]}>
            <Select options={[
              { value: 'planned', label: '计划执行' },
              { value: 'executed', label: '已执行' },
              { value: 'partial', label: '部分执行' },
              { value: 'not_executed', label: '未执行' },
            ]} />
          </Form.Item>
          {(executionStatus === 'executed' || executionStatus === 'partial') && (
            <Row gutter={12}>
              <Col span={12}><Form.Item name="executed_date" label="成交日期" rules={[{ required: true, message: '请填写成交日期' }]}><input type="date" style={{ width: '100%', height: 32, borderRadius: 6, border: '1px solid #d9d9d9', padding: '0 8px' }} /></Form.Item></Col>
              <Col span={12}><Form.Item name="executed_amount" label="成交金额"><InputNumber min={0} step={100} style={{ width: '100%' }} addonAfter="元" /></Form.Item></Col>
              <Col span={12}><Form.Item name="executed_shares" label="成交份额"><InputNumber min={0} step={100} style={{ width: '100%' }} addonAfter="份" /></Form.Item></Col>
              <Col span={12}><Form.Item name="executed_nav" label="成交净值"><InputNumber min={0} step={0.0001} precision={4} style={{ width: '100%' }} /></Form.Item></Col>
              <Col span={12}><Form.Item name="executed_fee" label="成交费用"><InputNumber min={0} step={1} style={{ width: '100%' }} addonAfter="元" /></Form.Item></Col>
              <Col span={12}><Form.Item name="execution_channel" label="执行渠道"><Input placeholder="如：支付宝/天天基金/券商" /></Form.Item></Col>
            </Row>
          )}
          {executionStatus === 'planned' && (
            <Alert type="info" showIcon message="计划执行" description="可先记录计划，成交后再回来补充成交日期、金额和份额。" style={{ marginBottom: 12 }} />
          )}
          {executionStatus === 'not_executed' && (
            <Form.Item name="not_executed_reason" label="未执行原因" rules={[{ required: true, message: '请填写未执行原因' }]}>
              <Input.TextArea rows={2} placeholder="例如：资金不足、风险偏好变化、未到操作时间" />
            </Form.Item>
          )}
          <Form.Item name="deviation_reason" label="偏离参考原因">
            <Input.TextArea rows={2} placeholder="实际金额/份额与参考结果不同的原因，可选" />
          </Form.Item>
          <Form.Item name="user_note" label="备注">
            <Input.TextArea rows={2} placeholder="补充说明，可选" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
