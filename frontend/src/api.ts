import type { ChatMessage, ChatResponse, HealthResponse } from './types';

/** 契约：上游 DeepSeek 超时 60s，前端留出余量，70s 后主动断开。 */
export const CHAT_TIMEOUT_MS = 70_000;
/** 健康检查只用于顶部徽章，失败要尽快显示「离线」，不需要等太久。 */
export const HEALTH_TIMEOUT_MS = 8_000;

/** 所有请求都走相对路径：开发环境由 vite proxy 转发，生产由 netlify.toml redirect 代理。 */
async function requestJson<T>(
  path: string,
  init: RequestInit,
  timeoutMs: number
): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    let response: Response;
    try {
      response = await fetch(path, { ...init, signal: controller.signal });
    } catch {
      if (controller.signal.aborted) {
        throw new Error(
          `请求超时（${Math.round(timeoutMs / 1000)} 秒），请稍后重试`
        );
      }
      throw new Error('网络连接失败，请检查网络后重试');
    }

    if (!response.ok) {
      throw new Error(await describeHttpError(response));
    }

    try {
      return (await response.json()) as T;
    } catch {
      throw new Error('返回数据解析失败，请稍后重试');
    }
  } finally {
    clearTimeout(timer);
  }
}

async function describeHttpError(response: Response): Promise<string> {
  const status = response.status;

  if (status === 429) return '请求太频繁，请稍后再试';
  if (status === 502 || status === 503 || status === 504) {
    return 'AI 服务暂时不可用，请稍后重试';
  }

  const detail = await readDetail(response);
  if (detail) return detail;

  if (status === 400 || status === 422) return '请求格式有误，请刷新页面后重试';
  if (status >= 500) return '服务器开小差了，请稍后重试';
  return `请求失败（HTTP ${status}），请稍后重试`;
}

async function readDetail(response: Response): Promise<string> {
  try {
    const data: unknown = await response.json();
    if (
      data &&
      typeof data === 'object' &&
      'detail' in data &&
      typeof (data as { detail: unknown }).detail === 'string' &&
      (data as { detail: string }).detail.trim()
    ) {
      return (data as { detail: string }).detail.trim();
    }
  } catch {
    // 非 JSON 响应（例如网关返回 HTML），交给上层兜底文案。
  }
  return '';
}

/** GET /api/health —— 数据版本徽章与英雄/装备数量。 */
export function getHealth(): Promise<HealthResponse> {
  return requestJson<HealthResponse>('/api/health', { method: 'GET' }, HEALTH_TIMEOUT_MS);
}

/** POST /api/chat —— 发送完整对话历史，拿回 reply + trace。 */
export async function sendChat(messages: ChatMessage[]): Promise<ChatResponse> {
  const data = await requestJson<ChatResponse>(
    '/api/chat',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages })
    },
    CHAT_TIMEOUT_MS
  );

  if (!data || typeof data.reply !== 'string') {
    throw new Error('返回数据格式异常，请稍后重试');
  }

  return {
    reply: data.reply,
    trace: Array.isArray(data.trace) ? data.trace : [],
    model: typeof data.model === 'string' ? data.model : ''
  };
}
