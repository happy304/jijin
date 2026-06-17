import axios, { AxiosError, InternalAxiosRequestConfig, AxiosResponse } from 'axios';
import { message } from 'antd';

/**
 * Axios instance configured with base URL from environment variable,
 * request/response interceptors, and unified error handling.
 */
const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Request interceptor: attach auth token if available (reserved for future use)
apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    const token = localStorage.getItem('token');
    if (token && config.headers) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error: AxiosError) => {
    return Promise.reject(error);
  },
);

// Response interceptor: unified error handling
apiClient.interceptors.response.use(
  (response: AxiosResponse) => {
    return response;
  },
  (error: AxiosError<ApiErrorResponse>) => {
    if (error.response) {
      const { status, data } = error.response;
      const rawDetail = data?.detail;
      const apiError = data?.error;
      const errorMessage = typeof rawDetail === 'string'
        ? rawDetail
        : (apiError?.message || data?.message || getDefaultErrorMessage(status));

      switch (status) {
        case 401:
          // Future: redirect to login
          message.error('认证已过期，请重新登录');
          localStorage.removeItem('token');
          break;
        case 403:
          message.error('没有权限执行此操作');
          break;
        case 404:
          message.error('请求的资源不存在');
          break;
        case 422:
          // Show detailed validation error if available
          if (typeof errorMessage === 'string' && errorMessage !== '请求参数有误') {
            message.error(errorMessage);
          } else if (Array.isArray(data?.detail)) {
            const firstErr = data.detail[0];
            const field = firstErr?.loc?.slice(-1)[0] || '';
            const msg = firstErr?.msg || '参数验证失败';
            message.error(`请求参数有误: ${field} ${msg}`);
          } else {
            message.error(errorMessage || '请求参数有误');
          }
          break;
        case 409:
          message.warning(errorMessage || '当前任务已在等待或运行中，请稍后查看结果');
          break;
        case 429:
          message.error('请求过于频繁，请稍后再试');
          break;
        case 500:
        case 502:
        case 503:
          message.error(errorMessage || '服务器错误，请稍后再试');
          break;
        default:
          message.error(errorMessage);
      }
    } else if (error.code === 'ECONNABORTED') {
      message.error('请求超时，请检查网络连接');
    } else if (!error.response) {
      message.error('网络连接失败，请检查网络');
    }

    return Promise.reject(error);
  },
);

function getDefaultErrorMessage(status: number): string {
  const messages: Record<number, string> = {
    400: '请求参数错误',
    401: '未授权',
    403: '禁止访问',
    404: '资源不存在',
    500: '服务器内部错误',
    502: '网关错误',
    503: '服务不可用',
  };
  return messages[status] || `请求失败 (${status})`;
}

/** Standard API error response shape from the backend */
export interface ApiErrorResponse {
  detail?: string | Array<{ loc?: (string | number)[]; msg?: string; type?: string }>;
  error?: {
    code?: string;
    message?: string;
    request_id?: string;
  };
  message?: string;
  code?: string;
}

export default apiClient;
