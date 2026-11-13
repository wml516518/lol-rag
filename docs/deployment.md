# 部署与运维 Runbook

本文档记录 LOL-RAG 的部署架构、手动发布命令、CI/CD 流程与常见故障处理。
面向「clone 下来之后怎么把它跑起来/发出去」，前端/后端细节见根目录 README。

## 1. 架构一览

```
浏览器 ──► Netlify（React 静态站点，lol-rag.netlify.app）
              │  /api/* 同源代理（netlify.toml + public/_redirects）
              ▼
           Render（FastAPI，lol-rag-api.onrender.com）
              ├── data/lol.db（提交进仓库的结构化数据）
              ├── knowledge/（示例攻略语料）+ 内存检索索引
              └── DeepSeek API（key 只存在于 Render 环境变量）
```

## 2. 环境变量

| 变量 | 配置位置 | 必填 | 说明 |
| --- | --- | --- | --- |
| `DEEPSEEK_API_KEY` | Render 控制台 | 是 | 不写进仓库；`render.yaml` 里 `sync: false` |
| `ALLOWED_ORIGINS` | Render | 否 | CORS 来源，默认 `*`；上线后可收紧为 Netlify 域名 |
| `LOL_DB_PATH` | Render | 否 | 默认 `<repo>/data/lol.db` |
| `DEEPSEEK_MODEL` | Render | 否 | 默认 `deepseek-chat` |

本地开发只需要在仓库根放 `.env`（写入 `DEEPSEEK_API_KEY`），`.env` 已被 gitignore。

## 3. 手动部署

### 前端（Netlify）

```bash
cd frontend
npm ci
npm run build                # 产物在 frontend/dist
netlify deploy --prod --dir dist --site lol-rag
```

站点根目录为 `frontend`、构建命令 `npm run build`、发布目录 `dist`；
`/api/*` 由 `frontend/public/_redirects`（随 dist 上传）代理到 Render。

### 后端（Render）

```bash
# 方式 A：Dashboard 里点 Manual Deploy
# 方式 B：CLI（需已 render login）
render deploys create srv-dbr64puc6qcc73cuhq5g --confirm
```

服务配置：`rootDir=backend`、`plan=free`、`region=singapore`、
`buildCommand=pip install -r requirements.txt`、
`startCommand=uvicorn app.main:app --host 0.0.0.0 --port $PORT`、健康检查 `/api/health`。

## 4. CI/CD

- **CI（GitHub Actions）**：`.github/workflows/ci.yml` 在 push / PR 时执行：
  - 后端 pytest（22 个用例，假 LLM，不触网）
  - `src/` 工具与检索测试（47 个用例，临时 SQLite / 临时索引）
  - 前端 `npm ci && npm run test && npm run build`
- **CD（平台原生自动部署）**：
  - Render：服务 `autoDeploy=yes`，push 到 `main` 自动重新构建后端
  - Netlify：站点已关联 GitHub 仓库，push 到 `main` 自动构建发布前端
- **发布后自检**：

```bash
curl -s https://lol-rag-api.onrender.com/api/health
# → {"status":"ok","data_version":"...","champions":173,"items":254,...}

curl -s -X POST https://lol-rag.netlify.app/api/chat \
  -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"亚索怎么打？"}]}' | jq '.trace[].tool'
# → "get_champion" "search_knowledge"（M3 路由生效）
```

## 5. 常见故障

### 5.1 Netlify：`Account credit usage exceeded - new deploys are blocked`

Free plan 的已知 stale credit flag：账号 API 显示 `plan_credits=300`、
`usages_exceeded=[]`，但所有生产部署被拒，Deploy Preview 却能正常构建。

处理方式：
1. 到 <https://answers.netlify.com/c/netlify-support/48> 发帖，附账号 slug、站点 id 与报错原文，
   请官方清除 stale flag（免费）；
2. 或升级付费计划（即时生效）；
3. 期间可用 Deploy Preview 验证前端构建是否正常，或本地 `npm run dev` 预览。

### 5.2 Render 冷启动

free plan 无流量时休眠，首个请求需要 40–60 秒；前端首条回复慢属正常现象。

### 5.3 本地 httpx 报 `Invalid port: ':1]'`

部分开发机 `NO_PROXY` 里带 `[::1]`，httpx 解析失败。运行以下命令前加：

```bash
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 <命令>
```

### 5.4 检索结果为空

- 确认 `knowledge/guides/` 存在且 `data/lol.db` 有数据；
- 索引按语料指纹在首次检索时重建（约 0.06s），删除 `data/knowledge_index.json` 可强制重建；
- 检索是字面 n-gram + TF-IDF 方案，语义同义词覆盖有限，可在语料中补充自然问法。

## 6. 回滚

- 前端：Netlify Deploys 页面选择上一个 `ready` 部署 → "Publish deploy"；
- 后端：Render Deploys 页面选择上一个 `live` 部署 → "Redeploy"；
- 代码：`git revert <sha>` 后 push，触发自动部署。
