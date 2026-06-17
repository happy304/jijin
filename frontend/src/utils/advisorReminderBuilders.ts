import type {
  AdvisorHistoryDetailResponse,
  AdvisorReminder,
  TradingAdviceItem,
} from '@/api/advisor';
import { getExecutionPlanTasks } from '@/components/AdvisorExecutionRecordsCard';
import type {
  AdvisorReminderCategory,
  AdvisorReminderCenterItem,
} from '@/components/AdvisorReminderCenter';

export function normalizeServerReminder(item: AdvisorReminder): AdvisorReminderCenterItem {
  const category = (['validity', 'risk', 'execution', 'plan'].includes(String(item.category))
    ? item.category
    : 'plan') as AdvisorReminderCategory;
  const level = (['info', 'warning', 'error', 'success'].includes(String(item.severity))
    ? item.severity
    : 'info') as AdvisorReminderCenterItem['level'];
  return {
    key: `server-${item.id}-${item.reminder_type}`,
    level,
    category,
    title: item.title,
    description: item.description,
  };
}

function daysUntilDate(value: string | null | undefined): number | null {
  if (!value) return null;
  const target = new Date(`${value}T23:59:59+08:00`);
  if (Number.isNaN(target.getTime())) return null;
  const now = new Date();
  const diff = target.getTime() - now.getTime();
  return Math.ceil(diff / (24 * 60 * 60 * 1000));
}

export function buildAdviceReminders(
  advices: TradingAdviceItem[],
  detail?: AdvisorHistoryDetailResponse | null,
): AdvisorReminderCenterItem[] {
  const reminders: AdvisorReminderCenterItem[] = [];
  const expiring = advices
    .map((advice) => ({ advice, days: daysUntilDate(advice.validity?.valid_until) }))
    .filter((item) => item.days != null && item.days <= 2);
  if (expiring.length > 0) {
    const expiredCount = expiring.filter((item) => (item.days ?? 0) < 0).length;
    reminders.push({
      key: 'validity-expiring',
      level: expiredCount > 0 ? 'error' : 'warning',
      category: 'validity',
      title: expiredCount > 0 ? '部分检查结果已过有效期' : '检查结果即将过期',
      description: expiredCount > 0
        ? `${expiredCount} 条基金检查结果已超过有效期，宜先刷新再参考。`
        : `${expiring.length} 条基金检查结果将在 2 天内到期，参考前请优先核对时效。`,
    });
  }

  const poorQuality = advices.filter((advice) => advice.data_quality?.status === 'poor');
  if (poorQuality.length > 0) {
    reminders.push({
      key: 'quality-poor',
      level: 'error',
      category: 'risk',
      title: '存在数据质量较差的检查结果',
      description: `${poorQuality.length} 条检查结果的数据质量为“较差”，更适合先观察或等待数据更新。`,
    });
  }

  const highOverfit = advices.filter((advice) => advice.overfit_risk?.level === 'high');
  if (highOverfit.length > 0) {
    reminders.push({
      key: 'overfit-high',
      level: 'warning',
      category: 'risk',
      title: '存在高过拟合风险信号',
      description: `${highOverfit.length} 条检查结果带有高过拟合风险，宜结合 OOS/PBO 审计谨慎参考。`,
    });
  }

  const batchPlans = advices.filter((advice) => advice.trade_plan?.execution_type === 'batch');
  if (batchPlans.length > 0) {
    const sample = batchPlans[0].trade_plan;
    reminders.push({
      key: 'batch-plan',
      level: 'info',
      category: 'plan',
      title: '包含分批参考计划',
      description: `当前有 ${batchPlans.length} 条检查结果采用分批参考方式${sample?.batch_interval_days ? `，参考间隔约 ${sample.batch_interval_days} 天` : ''}。`,
    });
  }

  const watchOnly = advices.filter((advice) => advice.action === 'watch').length;
  if (watchOnly > 0) {
    reminders.push({
      key: 'watch-actions',
      level: 'info',
      category: 'plan',
      title: '包含观察项',
      description: `${watchOnly} 条检查结果当前更适合持续观察，不宜作为立即大额操作依据。`,
    });
  }

  const allTasks = advices.flatMap((advice) => getExecutionPlanTasks(advice, detail));
  const pendingTasks = allTasks.filter((task) => task.status === 'pending');
  const overdueTasks = pendingTasks.filter((task) => {
    const days = daysUntilDate(task.scheduled_date);
    return days != null && days < 0;
  });
  const upcomingTasks = pendingTasks.filter((task) => {
    const days = daysUntilDate(task.scheduled_date);
    return days != null && days >= 0 && days <= 3;
  });

  if (overdueTasks.length > 0) {
    reminders.push({
      key: 'plan-overdue',
      level: 'warning',
      category: 'plan',
      title: '存在逾期未处理的参考计划',
      description: `${overdueTasks.length} 个分批/复核计划已超过计划日期，可补记处理结果或刷新检查结果。`,
    });
  } else if (upcomingTasks.length > 0) {
    reminders.push({
      key: 'plan-upcoming',
      level: 'info',
      category: 'plan',
      title: '近期有待复核计划',
      description: `${upcomingTasks.length} 个参考计划将在未来 3 天内到期，可提前核对资金、费率和风险承受能力。`,
    });
  }

  return reminders.slice(0, 6);
}

export function buildActiveReminderListItems(items: AdvisorReminder[]) {
  return items.map((item) => ({
    ...item,
    ui: normalizeServerReminder(item),
  }));
}

export function buildHistoryReminders(detail: AdvisorHistoryDetailResponse): AdvisorReminderCenterItem[] {
  if (detail.reminders && detail.reminders.length > 0) {
    return detail.reminders.filter((item) => item.status === 'active').map(normalizeServerReminder).slice(0, 6);
  }
  const reminders = buildAdviceReminders(detail.advices || [], detail);
  const executionSummary = detail.execution_summary;
  if (!executionSummary || executionSummary.status === 'no_execution_records') {
    reminders.unshift({
      key: 'execution-missing',
      level: 'info',
      category: 'execution',
      title: '这条历史检查结果还没有执行记录',
      description: '补充执行记录后，系统才能区分模型参考结果表现与用户实际采纳/偏离。',
    });
  } else if (executionSummary.significant_deviation_count > 0) {
    reminders.unshift({
      key: 'execution-drift',
      level: 'warning',
      category: 'execution',
      title: '存在明显执行偏离',
      description: `本次检查有 ${executionSummary.significant_deviation_count} 条执行记录与参考调整金额存在明显偏离，复盘时请结合偏离原因查看。`,
    });
  }
  return reminders.slice(0, 6);
}
