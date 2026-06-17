import { create } from 'zustand';
import { fetchIngestStatus, type IngestTaskStatus } from '@/api/funds';

export interface IngestTask {
  taskId: string;
  state: string;
  progress: string;
  fundCode: string;
  fundName?: string;
  startedAt: number; // timestamp
}

interface IngestState {
  /** 正在进行的采集任务 Map<fundCode, IngestTask> */
  tasks: Map<string, IngestTask>;
  /** 在线搜索关键词（跨页面保持） */
  onlineKeyword: string;
  /** 是否显示在线搜索结果 */
  showOnlineResults: boolean;

  /** 设置在线搜索关键词 */
  setOnlineKeyword: (keyword: string) => void;
  /** 设置是否显示在线搜索结果 */
  setShowOnlineResults: (show: boolean) => void;
  /** 添加或更新采集任务 */
  setTask: (code: string, task: IngestTask) => void;
  /** 移除采集任务 */
  removeTask: (code: string) => void;
  /** 获取某个基金的采集任务 */
  getTask: (code: string) => IngestTask | undefined;
}

export const useIngestStore = create<IngestState>((set, get) => ({
  tasks: new Map(),
  onlineKeyword: '',
  showOnlineResults: false,

  setOnlineKeyword: (keyword) => set({ onlineKeyword: keyword }),
  setShowOnlineResults: (show) => set({ showOnlineResults: show }),

  setTask: (code, task) =>
    set((state) => {
      const next = new Map(state.tasks);
      next.set(code, task);
      return { tasks: next };
    }),

  removeTask: (code) =>
    set((state) => {
      const next = new Map(state.tasks);
      next.delete(code);
      return { tasks: next };
    }),

  getTask: (code) => get().tasks.get(code),
}));

// ---------------------------------------------------------------------------
// 轮询管理器（单例，不随组件卸载而销毁）
// ---------------------------------------------------------------------------

const pollTimers = new Map<string, ReturnType<typeof setInterval>>();

export function startIngestPolling(
  code: string,
  taskId: string,
  options?: {
    onSuccess?: (code: string, result: Record<string, unknown> | null) => void;
    onFailure?: (code: string, progress: string) => void;
  },
) {
  // 清除已有的轮询
  stopIngestPolling(code);

  const timer = setInterval(async () => {
    try {
      const status: IngestTaskStatus = await fetchIngestStatus(taskId);
      const store = useIngestStore.getState();

      store.setTask(code, {
        taskId,
        state: status.state,
        progress: status.progress || '',
        fundCode: code,
        fundName: store.getTask(code)?.fundName,
        startedAt: store.getTask(code)?.startedAt || Date.now(),
      });

      if (status.state === 'SUCCESS') {
        stopIngestPolling(code);
        const result = status.result as Record<string, unknown> | null;
        const recordsInserted = (result?.records_inserted as number) ?? 0;
        const failedCount = (result?.failed as number) ?? 0;

        if (failedCount > 0 && recordsInserted === 0) {
          store.setTask(code, {
            taskId,
            state: 'FAILURE',
            progress: '未采集到数据',
            fundCode: code,
            fundName: store.getTask(code)?.fundName,
            startedAt: store.getTask(code)?.startedAt || Date.now(),
          });
          options?.onFailure?.(code, '未采集到数据');
        } else {
          const progress = recordsInserted > 0
            ? `采集完成 (${recordsInserted} 条)`
            : '无新数据';
          store.setTask(code, {
            taskId,
            state: 'SUCCESS',
            progress,
            fundCode: code,
            fundName: store.getTask(code)?.fundName,
            startedAt: store.getTask(code)?.startedAt || Date.now(),
          });
          options?.onSuccess?.(code, result);
        }
      } else if (status.state === 'FAILURE') {
        stopIngestPolling(code);
        store.setTask(code, {
          taskId,
          state: 'FAILURE',
          progress: status.progress || '采集失败',
          fundCode: code,
          fundName: store.getTask(code)?.fundName,
          startedAt: store.getTask(code)?.startedAt || Date.now(),
        });
        options?.onFailure?.(code, status.progress || '采集失败');
      }
    } catch {
      // 轮询请求失败时不中断，继续重试
    }
  }, 2000);

  pollTimers.set(code, timer);
}

export function stopIngestPolling(code: string) {
  const timer = pollTimers.get(code);
  if (timer) {
    clearInterval(timer);
    pollTimers.delete(code);
  }
}

/** 检查某个基金是否正在轮询中 */
export function isPolling(code: string): boolean {
  return pollTimers.has(code);
}
