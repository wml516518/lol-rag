import { getHealth, sendChat } from './api';

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

it('keeps health and chat requests under the deployed app prefix', async () => {
  vi.stubEnv('BASE_URL', '/apps/lol-rag/');
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ status: 'ok' })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ reply: '锤石', trace: [], model: 'test' })));
  vi.stubGlobal('fetch', fetcher);
  await getHealth();
  expect(await sendChat([{ role: 'user', content: '锤石的Q是什么？' }])).toEqual({ reply: '锤石', trace: [], model: 'test' });
  expect(fetcher.mock.calls[0][0]).toBe('/apps/lol-rag/api/health');
  expect(fetcher.mock.calls[1][0]).toBe('/apps/lol-rag/api/chat');
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ messages: [{ role: 'user', content: '锤石的Q是什么？' }] });
});
