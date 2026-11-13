import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import type { HealthResponse } from './types';

const SUGGESTIONS = [
  '锤石的Q技能是什么？',
  '三相之力多少钱？',
  '有哪些加护甲的装备？',
  '亚索是什么定位？'
];

const health: HealthResponse = {
  status: 'ok',
  data_version: '16.22.1',
  champions: 173,
  items: 863,
  model: 'deepseek-chat'
};

const chatReply = '锤石的 Q 是「死亡判决」……';

const chatResponse = {
  reply: chatReply,
  trace: [
    {
      tool: 'get_champion',
      args: { name: '锤石' },
      ok: true,
      result: { id: 'Thresh', name: '锤石', title: '魂锁典狱长' }
    }
  ],
  model: 'deepseek-chat'
};

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body)
  } as unknown as Response;
}

function installFetchMock(options?: { chat?: () => Response | Promise<Response> }) {
  const mock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/api/health')) {
      return jsonResponse(health);
    }
    if (url.includes('/api/chat')) {
      return options?.chat ? options.chat() : jsonResponse(chatResponse);
    }
    throw new Error(`测试中出现了未 mock 的请求：${url}`);
  });

  vi.stubGlobal('fetch', mock);
  return mock;
}

let fetchMock: ReturnType<typeof installFetchMock>;

beforeEach(() => {
  fetchMock = installFetchMock();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('LOL-RAG App', () => {
  it('渲染品牌栏、数据版本徽章、空状态与四个建议问题', async () => {
    render(<App />);

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('LOL');
    expect(screen.getByText('英雄联盟数据 Agent')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '从一个问题开始' })).toBeInTheDocument();

    for (const suggestion of SUGGESTIONS) {
      expect(screen.getByText(suggestion)).toBeInTheDocument();
    }

    // 健康检查成功后，徽章显示后端返回的数据版本。
    expect(await screen.findByText(/数据版本 16\.22\.1/)).toBeInTheDocument();
  });

  it('Enter 发送、Shift+Enter 换行', async () => {
    const user = userEvent.setup();
    render(<App />);

    const input = screen.getByLabelText('输入你的问题');
    const chatCalls = () =>
      (fetchMock.mock.calls as unknown[][]).filter(
        ([url]) => String(url).includes('/api/chat')
      ).length;

    await user.type(input, '锤石的Q');
    await user.keyboard('{Shift>}{Enter}{/Shift}');
    expect(input).toHaveValue('锤石的Q\n');
    expect(chatCalls()).toBe(0);

    await user.keyboard('{Enter}');
    expect(await screen.findByText(chatReply)).toBeInTheDocument();
    expect(chatCalls()).toBe(1);
  });

  it('发送中禁用输入并显示打字状态', async () => {
    let resolveChat: ((value: Response) => void) | undefined;
    const pendingChat = new Promise<Response>((resolve) => {
      resolveChat = resolve;
    });
    installFetchMock({ chat: () => pendingChat });

    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText('输入你的问题'), '三相之力多少钱？');
    await user.click(screen.getByRole('button', { name: /发送/ }));

    expect(screen.getByRole('status')).toHaveTextContent('正在查询数据…');
    expect(screen.getByRole('button', { name: /回答中/ })).toBeDisabled();
    expect(screen.queryByRole('heading', { name: '从一个问题开始' })).not.toBeInTheDocument();

    resolveChat?.(jsonResponse(chatResponse));
    expect(await screen.findByText(chatReply)).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('健康检查失败时版本徽章显示「离线」', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      })
    );

    render(<App />);

    expect(await screen.findByRole('button', { name: '离线' })).toBeInTheDocument();
  });

  it('点击建议问题后显示用户消息、助手回复与可折叠的工具轨迹', async () => {
    const user = userEvent.setup();
    render(<App />);
    await screen.findByText(/数据版本 16\.22\.1/);

    await user.click(screen.getByRole('button', { name: /锤石的Q技能是什么/ }));

    expect(await screen.findByText('锤石的Q技能是什么？')).toBeInTheDocument();
    expect(await screen.findByText(chatReply)).toBeInTheDocument();

    // 助手消息下方是原生 details/summary，可折叠的工具调用轨迹。
    const summary = screen.getByText('工具调用');
    const details = summary.closest('details');
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute('open');
    expect(screen.getByText('查询英雄')).toBeInTheDocument();
    expect(screen.getByText('锤石')).toBeInTheDocument();
    expect(screen.getByText(/已找到 · 锤石（魂锁典狱长）/)).toBeInTheDocument();

    expect(screen.getByRole('button', { name: /清空/ })).toBeInTheDocument();
  });

  it('502 时显示 AI 服务不可用的中文提示，并保留已发出的对话', async () => {
    installFetchMock({
      chat: () => jsonResponse({ detail: 'AI 服务暂时不可用，请稍后重试' }, 502)
    });

    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText('输入你的问题'), '锤石的Q技能是什么？');
    await user.click(screen.getByRole('button', { name: /发送/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'AI 服务暂时不可用，请稍后重试'
    );
    expect(screen.getByText('锤石的Q技能是什么？')).toBeInTheDocument();

    // 错误提示可以手动关闭，且不影响已有对话。
    await user.click(screen.getByRole('button', { name: '关闭错误提示' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByText('锤石的Q技能是什么？')).toBeInTheDocument();
  });

  it('429 时显示中文提示，并保留已经发出的对话', async () => {
    installFetchMock({
      chat: () => jsonResponse({ detail: '请求太频繁，请稍后再试' }, 429)
    });

    const user = userEvent.setup();
    render(<App />);

    await user.type(screen.getByLabelText('输入你的问题'), '三相之力多少钱？');
    await user.click(screen.getByRole('button', { name: /发送/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent('请求太频繁，请稍后再试');
    // 失败后不清空历史：用户消息仍在，空状态不再显示。
    expect(screen.getByText('三相之力多少钱？')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '从一个问题开始' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /清空/ })).toBeInTheDocument();
  });
});
