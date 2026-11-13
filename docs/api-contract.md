# LOL-RAG 前后端契约（v1，冻结）

> 本文件是 core / api / web 三个开发任务的唯一接口真相。任何一方要改契约，
> 必须先告知 Lead，由 Lead 统一改本文件并通知其他任务。
> 提交到仓库后它就是项目文档的一部分。

## 1. 目录职责

```
lol-rag/
├── src/                 # 学习主线 M0/M1：数据拉取、SQLite、工具、Agent 循环、CLI
├── backend/             # FastAPI 后端（Render 部署）
├── frontend/            # React + Vite 前端（Netlify 部署）
├── data/                # 由 src/fetch_data.py 生成；lol.db 提交进仓库，原始 json 不提交
├── docs/                # 文档（本文件、部署说明）
└── render.yaml          # Render 部署声明
```

## 2. 数据层（M0，刷新到最新版本）

- 数据源：Data Dragon，`zh_CN`，版本取 `api/versions.json[0]`（当前 16.22.1）
- 除 `champion.json` / `item.json` 外，**新增拉取 `championFull.json`**（一次请求，含技能与被动）
- `champions` 表新增 `spells TEXT`（JSON 字符串），结构：
  ```json
  [{"key": "P", "name": "锐利之刃", "description": "..."},
   {"key": "Q", "name": "...", "description": "..."},
   {"key": "W", ...}, {"key": "E", ...}, {"key": "R", ...}]
  ```
- `items` 表新增 `description TEXT`：把 `item.json` 的 `description` 字段去 HTML 标签、
  反转义 HTML 实体后的纯文本（用于回答“这件装备有什么效果”）
- 归一化后验收：英雄 173 / 装备 **254** / 版本为最新；`meta.version` 正确
  - 装备刻意**过滤为「召唤师峡谷可购买」**（`maps["11"] == true` 且 `gold.purchasable == true`）：
    `item.json` 全量 863 件里有「任务：上路」「先锋之眼」等任务/娱乐模式/已下架道具，
    不过滤会让 `find_items_by_tag` 返回垃圾结果。过滤逻辑保留在代码里并注释说明取舍。
- 数据库文件 `data/lol.db` 约 300KB，**提交进 Git**（前端/后端/评审者拿到仓库即可跑）

## 3. 工具函数（M1 填空 ③，`src/tools.py`）

失败一律 `raise ValueError("中文、给模型看的说明")`，由 Agent 循环捕获回给模型。

```python
get_champion(name: str) -> dict
# 匹配顺序：id 精确(忽略大小写) → name 精确 → title 精确 → name/title LIKE '%name%'
# 返回：
{"id": "Thresh", "name": "锤石", "title": "魂锁典狱长",
 "tags": ["Support", "Tank"], "blurb": "...", "spells": [{"key","name","description"}, ...]}

get_item(name: str) -> dict
# 同上匹配顺序（name 精确 → LIKE）；返回：
{"id": 3031, "name": "无尽之刃", "gold": 3450,
 "plaintext": "...", "description": "纯文本效果说明", "tags": ["Damage", "CriticalStrike"]}

find_items_by_tag(tag: str) -> list[dict]
# 中文先经 TAG_CN2EN 转英文，再大小写不敏感匹配 tags；未知类别 raise ValueError
# 返回 [{"id","name","gold","plaintext","tags"}, ...]，最多 30 件，按 gold 升序
```

## 4. Agent 层（M1 填空 ④⑤，`src/agent.py`）

- `TOOL_SPECS` 保持现有三个工具（description 可优化，参数结构不变）
- `SYSTEM_PROMPT`（④）：中文 LOL 助手；**任何英雄/装备/数值/技能相关事实必须先调工具，
  禁止凭记忆编造**；不在数据内就直说“我的数据里没有”；回答精炼，可用要点列表；
  引用数据版本号；禁止输出工具调用过程本身
- `dispatch(name, args)`（⑤）：白名单映射到 `tools` 三个函数，未知工具
  `raise ValueError(f"未知工具 {name}")`；不要用 `eval`/`getattr` 动态执行

## 5. 后端 API（Render，`backend/`）

技术：FastAPI + uvicorn + httpx。Python 3.13。启动命令见 `render.yaml`。

### GET `/api/health`
```json
{"status": "ok", "data_version": "16.22.1",
 "champions": 173, "items": 254, "model": "deepseek-chat"}
```

### POST `/api/chat`
请求：
```json
{"messages": [{"role": "user", "content": "锤石的Q技能是什么？"}]}
```
- `role` 仅允许 `user` / `assistant`；`content` 1..2000 字符；最多 20 条（超出的旧消息截断）
- 后端自己追加 system prompt，不信任前端传入的 system 消息

响应 200：
```json
{"reply": "锤石的Q是「死亡判决」……",
 "trace": [{"tool": "get_champion", "args": {"name": "锤石"},
            "ok": true, "result": {"id": "Thresh", "name": "锤石", "spells": []}}],
 "model": "deepseek-chat"}
```
- `trace` 只保留本轮工具调用，顺序即调用顺序；失败调用为
  `{"tool": "...", "args": {...}, "ok": false, "error": "未找到英雄 xxx"}`；不返回内部堆栈
- 工具循环最多 6 轮，上游超时 60s；DeepSeek 异常 → 502
  `{"detail": "AI 服务暂时不可用，请稍后重试"}`
- 限流：同 IP 每分钟 20 次，超出 → 429 `{"detail": "请求太频繁，请稍后再试"}`（内存实现即可）
- CORS：`ALLOWED_ORIGINS` 环境变量（逗号分隔，默认 `*`）；不启用 credentials
- 日志：不得出现 `DEEPSEEK_API_KEY`；只记工具名、耗时、状态码
- 若 `data/lol.db` 不存在 → 启动失败并给出明确中文提示（不要静默降级）

### 环境变量
| 变量 | 说明 |
|---|---|
| `DEEPSEEK_API_KEY` | 必填，Render 后台配置，禁止提交 |
| `ALLOWED_ORIGINS` | 默认 `*` |
| `LOL_DB_PATH` | 可选，默认 `<repo>/data/lol.db` |
| `DEEPSEEK_MODEL` | 可选，默认 `deepseek-chat` |

### 复用原则
后端不复制 prompt/工具逻辑，直接 `sys.path.insert(0, <repo>/src)` 后复用
`agent.TOOL_SPECS`、`agent.SYSTEM_PROMPT`、`agent.dispatch`、`agent.MODEL`，
只在自己的 `chat.py` 里实现带 trace 的循环。`agent.run()` 保持给 CLI 用。

## 6. 前端（Netlify，`frontend/`）

技术：React 18 + Vite + TypeScript，纯 CSS（不引 UI 框架），可选 `lucide-react` 图标。

- 开发：`npm run dev`，`vite.config.ts` 里 `server.proxy['/api'] → http://127.0.0.1:8000`
- 生产：请求相对路径 `/api/chat`；`netlify.toml` 用 redirect 代理到 Render：
  ```toml
  [[redirects]]
    from = "/api/*"
    to = "https://lol-rag-api.onrender.com/api/:splat"   # 当前已部署的 Render 服务
    status = 200
    force = true
  ```
  （因此前端构建不需要环境变量，仓库连 GitHub 后也能自动构建）
- 页面要求：
  - 顶部：项目名 LOL-RAG / 副标题「英雄联盟数据 Agent」/ 数据版本徽章
  - 主体：对话区（用户/助手气泡）、助手消息下方展示可折叠的「工具调用」轨迹
    （工具中文名 + 参数 + 关键结果，例如「查询英雄 · 锤石 → 已找到」）
  - 输入：多行输入框 + 发送按钮，Enter 发送 / Shift+Enter 换行，请求中禁用并显示打字状态
  - 空状态：4 个建议问题快捷 chips，例如「锤石的Q技能是什么？」「三相之力多少钱？」
    「有哪些加护甲的装备？」「亚索是什么定位？」
  - 错误：网络错误/429/502 用中文提示，不刷屏、不清空已有多轮对话
  - 移动端适配；深色主题（LOL 风格：深蓝黑 + 金色点缀），不使用官方 LOGO 素材
  - 页脚：数据来源 Data Dragon、代码仓库链接（Lead 部署后补）
- `npm run build` 必须零错误；`npm run test` 至少 1 个冒烟测试（渲染 + 发送流程 mock fetch）

## 7. 部署

- **Render**：仓库根 `render.yaml`，rootDir `backend`，健康检查 `/api/health`，
  free plan（冷启动可接受，README 写明）
- **Netlify**：站点根目录 `frontend`（CLI 部署时 `--dir frontend/dist`），
  build `npm run build`，publish `dist`；redirect 代理见上
- 密钥只存在 Render 环境变量；仓库任何位置不得出现真实 key

## 8. 验收清单（Lead 会用这套命令复核）

```bash
# 1. 数据
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 uv run python src/fetch_data.py
# → 打印 ✅ 最新版本 / 英雄 / 装备，data/lol.db 存在

# 2. 工具单测
uv run python -m pytest tests/test_tools.py -q

# 3. Agent CLI 冒烟（真实 key）
echo "锤石的Q技能是什么？" | NO_PROXY=localhost,127.0.0.1 uv run python src/agent.py

# 4. 后端
cd backend && pip install -r requirements.txt
uvicorn app.main:app --port 8000 &
curl -s localhost:8000/api/health
curl -s -X POST localhost:8000/api/chat -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"三相之力多少钱？"}]}'

# 5. 前端
cd frontend && npm install && npm run build && npm run test
```

### 本机环境坑（必须知道）
- 本机 `NO_PROXY` 含 `[::1]`，**httpx 会解析报错** `Invalid port: ':1]'`；
  所有本地 httpx/uvicorn/pytest 命令前加：
  `NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1`
  （curl 不受影响）
- npm 的 `~/.npm` 缓存目录权限异常，安装依赖时用
  `npm_config_cache=/tmp/npm-cache npm install`（若失败）

## 9. 禁止事项

- 不要 `git add/commit/push`，不要部署（Lead 统一做）
- 不要改别人负责目录；需要改动先发消息给 Lead
- 不要 `cat .env` / 打印 API key / 把 key 写进任何文件（含测试、README、yaml）
- 不要引入重型依赖（向量库、UI 框架、ORM）；M2/M3 不在本次范围
