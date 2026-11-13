/** 前后端契约类型，与 docs/api-contract.md 第 5、6 节保持一致。 */

export type ChatRole = 'user' | 'assistant';

/** 发送给 POST /api/chat 的一条消息。 */
export type ChatMessage = {
  role: ChatRole;
  content: string;
};

/** 单次工具调用轨迹；失败时没有 result，只有 error。 */
export type ToolTrace = {
  tool: string;
  args: Record<string, unknown>;
  ok: boolean;
  result?: unknown;
  error?: string;
};

/** POST /api/chat 的 200 响应。 */
export type ChatResponse = {
  reply: string;
  trace: ToolTrace[];
  model: string;
};

/** GET /api/health 的响应。 */
export type HealthResponse = {
  status: string;
  data_version: string;
  champions: number;
  items: number;
  model: string;
};

/** 页面内部维护的一轮对话（含界面展示所需的元信息）。 */
export type ChatTurn = {
  id: string;
  role: ChatRole;
  content: string;
  trace?: ToolTrace[];
};

/** M2 知识库检索结果的知识来源类型。 */
export type KnowledgeKind = 'guide' | 'champion' | 'item' | string;

/** search_knowledge 返回的单条检索结果。 */
export type KnowledgeHit = {
  title: string;
  kind: KnowledgeKind;
  snippet: string;
  score: number;
};

/** search_knowledge 工具返回结构：{"query","results":[...]}。 */
export type SearchKnowledgeResult = {
  query: string;
  results: KnowledgeHit[];
};
