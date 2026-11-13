import {
  AlertTriangle,
  ChevronDown,
  Github,
  RotateCcw,
  Send
} from 'lucide-react';
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent
} from 'react';
import { getHealth, sendChat } from './api';
import { Markdown } from './Markdown';
import type {
  ChatMessage,
  ChatTurn,
  HealthResponse,
  KnowledgeHit,
  SearchKnowledgeResult,
  ToolTrace
} from './types';

const REPO_URL = 'https://github.com/wml516518/lol-rag';

type Suggestion = {
  text: string;
  /** M2/M3 特色问题：高亮展示，提示 Agent 会去检索知识库。 */
  featured?: boolean;
};

const SUGGESTIONS: Suggestion[] = [
  { text: '锤石的Q技能是什么？' },
  { text: '三相之力多少钱？' },
  { text: '有哪些加护甲的装备？' },
  { text: '亚索是什么定位？' },
  { text: '亚索怎么打？', featured: true }
];

const TOOL_LABELS: Record<string, string> = {
  get_champion: '查询英雄',
  get_item: '查询装备',
  find_items_by_tag: '按类别查装备',
  search_knowledge: '检索知识库'
};

/** M2 知识库来源类型 → 徽章文案。 */
const SOURCE_KIND_LABELS: Record<string, string> = {
  guide: '示例攻略',
  champion: '英雄档案',
  item: '装备档案'
};

function sourceKindLabel(kind: string): string {
  return SOURCE_KIND_LABELS[kind] ?? '知识库';
}

function truncate(text: string, max = 120): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

type HealthState = 'loading' | 'online' | 'offline';

let turnSeq = 0;
function nextTurnId(): string {
  turnSeq += 1;
  return `turn-${Date.now().toString(36)}-${turnSeq}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function toolLabel(tool: string): string {
  return TOOL_LABELS[tool] ?? tool;
}

function describeArgs(trace: ToolTrace): string {
  const args = isRecord(trace.args) ? trace.args : {};
  const name = typeof args.name === 'string' ? args.name.trim() : '';
  if (name) return name;
  const tag = typeof args.tag === 'string' ? args.tag.trim() : '';
  if (tag) return tag;

  const entries = Object.entries(args);
  if (entries.length === 0) return '无参数';
  return entries
    .map(([key, value]) => `${key}：${String(value)}`)
    .join('，')
    .slice(0, 48);
}

function getSearchResult(trace: ToolTrace): SearchKnowledgeResult | null {
  if (trace.tool !== 'search_knowledge' || !trace.ok || !isRecord(trace.result)) {
    return null;
  }
  const raw = trace.result;
  if (!Array.isArray(raw.results)) return null;

  const results: KnowledgeHit[] = raw.results
    .filter((hit): hit is Record<string, unknown> => isRecord(hit))
    .map((hit) => ({
      title: typeof hit.title === 'string' && hit.title.trim() ? hit.title : '未命名资料',
      kind: typeof hit.kind === 'string' ? hit.kind : 'guide',
      snippet: typeof hit.snippet === 'string' ? hit.snippet : '',
      score: typeof hit.score === 'number' ? hit.score : 0
    }));

  return {
    query: typeof raw.query === 'string' ? raw.query : '',
    results
  };
}

function summarizeResult(trace: ToolTrace): string {
  if (!trace.ok) {
    const reason = typeof trace.error === 'string' ? trace.error.trim() : '';
    return reason || '调用失败';
  }

  const result = trace.result;
  if (trace.tool === 'get_champion' && isRecord(result) && typeof result.name === 'string') {
    const title = typeof result.title === 'string' ? result.title : '';
    return title ? `已找到 · ${result.name}（${title}）` : `已找到 · ${result.name}`;
  }
  if (trace.tool === 'get_item' && isRecord(result) && typeof result.name === 'string') {
    const gold = typeof result.gold === 'number' ? ` · ${result.gold} 金币` : '';
    return `已找到 · ${result.name}${gold}`;
  }
  if (trace.tool === 'search_knowledge') {
    const search = getSearchResult(trace);
    if (!search) return '检索完成';
    if (search.results.length === 0) return '检索到 0 条';
    const titles = search.results
      .slice(0, 3)
      .map((hit) => hit.title)
      .join('、');
    return `检索到 ${search.results.length} 条 · ${titles}`;
  }
  if (Array.isArray(result)) {
    return `返回 ${result.length} 条结果`;
  }
  return '调用成功';
}

function BrandGlyph({ size = 40 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 64 64"
      fill="none"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M32 3.5 57 17.75v28.5L32 60.5 7 46.25v-28.5L32 3.5Z"
        stroke="url(#lolrag-gold)"
        strokeWidth="1.4"
      />
      <path d="M32 14 47 32 32 50 17 32 32 14Z" fill="url(#lolrag-gold-fill)" opacity="0.92" />
      <path d="M32 23 40 32l-8 9-8-9 8-9Z" fill="#070a12" opacity="0.55" />
      <defs>
        <linearGradient
          id="lolrag-gold"
          x1="10"
          y1="4"
          x2="54"
          y2="60"
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor="#f3d27f" />
          <stop offset="1" stopColor="#a87c35" />
        </linearGradient>
        <linearGradient
          id="lolrag-gold-fill"
          x1="17"
          y1="14"
          x2="47"
          y2="50"
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor="#efd089" />
          <stop offset="1" stopColor="#b4843a" />
        </linearGradient>
      </defs>
    </svg>
  );
}

function AgentGlyph() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M12 2.5 21 7.5v9L12 21.5 3 16.5v-9L12 2.5Z" stroke="currentColor" strokeWidth="1.3" />
      <path d="m12 7 4.5 5-4.5 5-4.5-5 4.5-5Z" fill="currentColor" opacity="0.85" />
    </svg>
  );
}

function EmptyRune() {
  return (
    <svg width="34" height="34" viewBox="0 0 48 48" fill="none" aria-hidden="true">
      <path d="M24 3 42 13v22L24 45 6 35V13L24 3Z" stroke="#d8b26a" strokeWidth="1.1" opacity="0.7" />
      <path d="M24 12 34 24 24 36 14 24 24 12Z" stroke="#d8b26a" strokeWidth="1.1" opacity="0.9" />
      <path d="M24 19 28 24l-4 5-4-5 4-5Z" fill="#d8b26a" opacity="0.8" />
    </svg>
  );
}

function VersionBadge({
  state,
  health,
  onRetry
}: {
  state: HealthState;
  health: HealthResponse | null;
  onRetry: () => void;
}) {
  if (state === 'loading') {
    return (
      <span className="version-badge is-loading">
        <span className="status-dot" aria-hidden="true" />
        数据连接中…
      </span>
    );
  }

  if (state === 'offline' || !health) {
    return (
      <button
        type="button"
        className="version-badge is-offline"
        onClick={onRetry}
        title="无法连接后端，点击重试"
      >
        <span className="status-dot" aria-hidden="true" />
        离线
      </button>
    );
  }

  return (
    <span
      className="version-badge is-online"
      title={`${health.champions} 位英雄 · ${health.items} 件装备`}
    >
      <span className="status-dot" aria-hidden="true" />
      数据版本 {health.data_version}
    </span>
  );
}

function ToolTraceList({ trace }: { trace: ToolTrace[] }) {
  if (trace.length === 0) return null;

  return (
    <details className="trace">
      <summary className="trace-summary">
        <ChevronDown size={14} className="trace-chevron" aria-hidden="true" />
        <span>工具调用</span>
        <span className="trace-count">{trace.length}</span>
        <span className="trace-hint">查看轨迹</span>
      </summary>
      <ol className="trace-list">
        {trace.map((item, index) => {
          const search = getSearchResult(item);
          return (
            <li
              key={`${item.tool}-${index}`}
              className={item.ok ? 'trace-item is-ok' : 'trace-item is-fail'}
            >
              <div className="trace-line">
                <span className="trace-tool">{toolLabel(item.tool)}</span>
                <code className="trace-arg">{describeArgs(item)}</code>
                <span className="trace-arrow" aria-hidden="true">
                  →
                </span>
                <span className="trace-result">{summarizeResult(item)}</span>
                <span className="trace-raw">{item.tool}</span>
              </div>

              {search && search.results.length > 0 && (
                <ul className="trace-results" aria-label="知识库检索结果">
                  {search.results.map((hit, hitIndex) => (
                    <li className="trace-hit" key={`${hit.title}-${hitIndex}`}>
                      <span className={`source-badge source-badge--${hit.kind}`}>
                        {sourceKindLabel(hit.kind)}
                      </span>
                      <span className="trace-hit-title">{hit.title}</span>
                      {hit.score > 0 && (
                        <span className="trace-hit-score" title="相关度">
                          {hit.score.toFixed(2)}
                        </span>
                      )}
                      {hit.snippet && (
                        <p className="trace-hit-snippet">{truncate(hit.snippet)}</p>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ol>
    </details>
  );
}

function MessageTurn({ turn }: { turn: ChatTurn }) {
  const isUser = turn.role === 'user';

  return (
    <article className={isUser ? 'turn turn--user' : 'turn turn--assistant'}>
      {!isUser && (
        <div className="avatar" aria-hidden="true">
          <AgentGlyph />
        </div>
      )}
      <div className="turn-body">
        <span className="turn-role">{isUser ? '你' : 'RAG 助手'}</span>
        <div className="bubble">
          {isUser ? <p className="plain-text">{turn.content}</p> : <Markdown text={turn.content} />}
        </div>
        {!isUser && turn.trace && <ToolTraceList trace={turn.trace} />}
      </div>
    </article>
  );
}

function TypingTurn() {
  return (
    <div className="turn turn--assistant">
      <div className="avatar" aria-hidden="true">
        <AgentGlyph />
      </div>
      <div className="typing">
        <span className="typing-dots" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
        <span role="status">正在查询数据…</span>
      </div>
    </div>
  );
}

function App() {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthState, setHealthState] = useState<HealthState>('loading');

  const listRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  const refreshHealth = useCallback(async () => {
    setHealthState('loading');
    try {
      const data = await getHealth();
      setHealth(data);
      setHealthState('online');
    } catch {
      setHealth(null);
      setHealthState('offline');
    }
  }, []);

  useEffect(() => {
    void refreshHealth();
  }, [refreshHealth]);

  useEffect(() => {
    const node = listRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [turns, pending, error]);

  useEffect(() => {
    const node = inputRef.current;
    if (!node) return;
    node.style.height = 'auto';
    node.style.height = `${Math.min(node.scrollHeight, 160)}px`;
  }, [input]);

  // 移动端键盘弹出时 visualViewport 会变矮：同步为 CSS 变量，
  // 让聊天面板跟着收缩，输入区始终留在键盘上方。
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;

    const syncHeight = () => {
      document.documentElement.style.setProperty(
        '--app-viewport-height',
        `${Math.round(viewport.height)}px`
      );
    };

    syncHeight();
    viewport.addEventListener('resize', syncHeight);
    return () => viewport.removeEventListener('resize', syncHeight);
  }, []);

  async function send(raw: string) {
    const content = raw.trim();
    if (!content || pending) return;

    const userTurn: ChatTurn = { id: nextTurnId(), role: 'user', content };
    const nextTurns = [...turns, userTurn];

    setTurns(nextTurns);
    setInput('');
    setError(null);
    setPending(true);

    const history: ChatMessage[] = nextTurns
      .map((turn) => ({ role: turn.role, content: turn.content }))
      .slice(-20);

    try {
      const response = await sendChat(history);
      setTurns((current) => [
        ...current,
        {
          id: nextTurnId(),
          role: 'assistant' as const,
          content: response.reply,
          trace: response.trace
        }
      ]);
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : '发送失败，请稍后重试'
      );
    } finally {
      setPending(false);
      inputRef.current?.focus();
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send(input);
  }

  function handleInputKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey) return;
    // 中文输入法选词时的回车不算发送。
    if (event.nativeEvent.isComposing) return;
    event.preventDefault();
    void send(input);
  }

  function handleClear() {
    if (pending) return;
    setTurns([]);
    setError(null);
    inputRef.current?.focus();
  }

  const composerDisabled = pending || input.trim().length === 0;
  const statusText =
    healthState === 'online' ? '已连接' : healthState === 'loading' ? '连接中' : '离线';

  return (
    <div className="app">
      <header className="site-header">
        <div className="header-inner">
          <div className="brand">
            <span className="brand-mark">
              <BrandGlyph />
            </span>
            <div className="brand-text">
              <h1 className="brand-title">
                LOL<span className="brand-dash">—</span>
                <span className="brand-title-accent">RAG</span>
              </h1>
              <p className="brand-sub">英雄联盟数据 Agent</p>
            </div>
          </div>

          <div className="header-meta">
            <VersionBadge state={healthState} health={health} onRetry={() => void refreshHealth()} />
            <a
              className="header-repo"
              href={REPO_URL}
              target="_blank"
              rel="noreferrer"
              title="GitHub 仓库（部署后补充真实地址）"
            >
              <Github size={15} aria-hidden="true" />
              <span>GitHub</span>
            </a>
          </div>
        </div>
      </header>

      <main className="main">
        <section className="intro">
          <p className="eyebrow">M4 · CHAT FRONTEND</p>
          <h2>
            每个答案，
            <br />
            都<em>查过数据</em>
          </h2>
          <p className="intro-lede">
            英雄、装备、技能与价格——回答之前，Agent 会先调用 Data Dragon
            工具核实事实，再把每一步调用轨迹摊开给你看。
          </p>
          <p className="data-stats">
            {healthState === 'online' && health ? (
              <>
                <span>
                  <strong>{health.champions}</strong> 位英雄
                </span>
                <span>
                  <strong>{health.items}</strong> 件装备
                </span>
                <span>
                  <strong>{health.data_version}</strong> 数据版本
                </span>
              </>
            ) : (
              <span className="data-stats-offline">
                {healthState === 'loading' ? '正在读取数据版本…' : '后端离线 · 等待连接'}
              </span>
            )}
          </p>
          <ul className="cred-list">
            <li>
              <span className="cred-bullet" aria-hidden="true">
                ◆
              </span>
              <span>
                <strong>工具调用轨迹</strong>全程可见，拒绝黑盒
              </span>
            </li>
            <li>
              <span className="cred-bullet" aria-hidden="true">
                ◆
              </span>
              <span>
                <strong>Data Dragon</strong> 官方中文数据源
              </span>
            </li>
            <li>
              <span className="cred-bullet" aria-hidden="true">
                ◆
              </span>
              <span>
                <strong>DeepSeek</strong> function calling 驱动
              </span>
            </li>
            <li>
              <span className="cred-bullet" aria-hidden="true">
                ◆
              </span>
              <span>
                <strong>知识库检索</strong>：示例攻略 / 英雄档案 / 装备档案
              </span>
            </li>
          </ul>
          <p className="intro-foot">
            非官方粉丝项目 · 不使用 Riot Games 官方 LOGO 与英雄素材
          </p>
        </section>

        <section className="chat-panel" aria-label="对话面板">
          <div className="panel-head">
            <div className="panel-title">
              <span className="panel-dot" data-state={healthState} aria-hidden="true" />
              <h2>对话</h2>
              <span className="panel-sub">{statusText}</span>
            </div>
            {turns.length > 0 && (
              <button
                type="button"
                className="ghost-btn"
                onClick={handleClear}
                disabled={pending}
              >
                <RotateCcw size={13} aria-hidden="true" />
                清空
              </button>
            )}
          </div>

          <div className="chat-scroll" ref={listRef} role="log" aria-live="polite">
            {turns.length === 0 && (
              <div className="empty-state">
                <span className="empty-mark">
                  <EmptyRune />
                </span>
                <p className="empty-eyebrow">LOL DATA AGENT</p>
                <h3>从一个问题开始</h3>
                <p className="empty-desc">
                  查询英雄技能、装备价格，或检索攻略与思路。回答只基于数据与知识库，不凭空生成。
                </p>
                <div className="chip-grid">
                  {SUGGESTIONS.map((suggestion, index) => (
                    <button
                      key={suggestion.text}
                      type="button"
                      className={suggestion.featured ? 'chip chip--featured' : 'chip'}
                      onClick={() => void send(suggestion.text)}
                      disabled={pending}
                    >
                      <span className="chip-index" aria-hidden="true">
                        {String(index + 1).padStart(2, '0')}
                      </span>
                      <span className="chip-text">{suggestion.text}</span>
                      {suggestion.featured && (
                        <span className="chip-badge" aria-label="检索增强">
                          RAG
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {turns.map((turn) => (
              <MessageTurn key={turn.id} turn={turn} />
            ))}

            {pending && <TypingTurn />}
          </div>

          {error && (
            <div className="error-bar" role="alert">
              <AlertTriangle size={15} aria-hidden="true" />
              <span>{error}</span>
              <button
                type="button"
                className="error-dismiss"
                onClick={() => setError(null)}
                aria-label="关闭错误提示"
              >
                ×
              </button>
            </div>
          )}

          <form className="composer" onSubmit={handleSubmit}>
            <div className="composer-field">
              <textarea
                ref={inputRef}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleInputKeyDown}
                placeholder="问点什么，例如：锤石的 Q 技能是什么？"
                aria-label="输入你的问题"
                rows={1}
                maxLength={2000}
              />
              <button type="submit" className="send-btn" disabled={composerDisabled}>
                <Send size={15} aria-hidden="true" />
                {pending ? '回答中' : '发送'}
              </button>
            </div>
            <div className="composer-hint">
              <span>Enter 发送 · Shift + Enter 换行</span>
              <span className={input.length > 1900 ? 'char-count is-warn' : 'char-count'}>
                {input.length} / 2000
              </span>
            </div>
          </form>
        </section>
      </main>

      <footer className="site-footer">
        <div className="footer-inner">
          <p>
            数据来源{' '}
            <a
              href="https://developer.riotgames.com/docs/lol#data-dragon"
              target="_blank"
              rel="noreferrer"
            >
              Data Dragon
            </a>
            <span className="footer-sep">·</span>
            非官方项目
          </p>
          <p className="footer-links">
            <a href={REPO_URL} target="_blank" rel="noreferrer">
              GitHub 仓库
            </a>
            <span className="footer-sep">·</span>
            React 18 + Vite
          </p>
        </div>
      </footer>
    </div>
  );
}

export default App;
