# LOL-RAG 后端（FastAPI，Render 部署）

接口契约见仓库根 [docs/api-contract.md](../docs/api-contract.md)（冻结版，字段名以它为准）。
后端不复制 prompt / 工具逻辑：`app/db_bridge.py` 把 `<repo>/src` 加入 `sys.path`，
`app/chat.py` 只实现带 trace 的 function calling 循环，复用 `src/agent.py` 的
`TOOL_SPECS`、`SYSTEM_PROMPT`、`dispatch`、`MODEL`、`API_URL`。

## 目录

```
backend/
├── app/
│   ├── main.py       # FastAPI 应用：/api/health、/api/chat、限流、CORS、启动自检
│   ├── chat.py       # 带 trace 的 DeepSeek function calling 循环（最多 6 轮工具）
│   ├── settings.py   # 环境变量 + 自动加载仓库根 .env（不打印 key）
│   ├── db_bridge.py  # 复用 <repo>/src，读 data/lol.db 的版本/数量
│   └── schemas.py    # 请求校验：role 仅 user/assistant、content 1..2000、最多 20 条
├── tests/            # TestClient + 假 LLM，完全离线
├── requirements.txt
├── pytest.ini
└── run.py            # 本地启动入口
```

## 本地启动

前提：仓库根已生成 `data/lol.db`（`uv run python src/fetch_data.py`），
仓库根 `.env` 里有 `DEEPSEEK_API_KEY=...`（只读，切勿提交）。

```bash
cd backend
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 本机 NO_PROXY 含 [::1] 会让 httpx 报 Invalid port，本地务必加前缀
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  uvicorn app.main:app --port 8000 --reload

curl -s localhost:8000/api/health
curl -s -X POST localhost:8000/api/chat -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"三相之力多少钱？"}]}'
```

也可以 `python run.py`（读 `PORT` 环境变量，默认 8000）。

> 已装好依赖时，本仓库的 `.venv` 或 `backend/.venv` 都能跑；
> 验收命令见契约第 8 节。

## 环境变量

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | 是 | — | 只配在 Render 控制台 / 本地仓库根 `.env` |
| `ALLOWED_ORIGINS` | 否 | `*` | 逗号分隔的 CORS 来源；不启用 credentials |
| `LOL_DB_PATH` | 否 | `<repo>/data/lol.db` | 相对路径以仓库根为基准 |
| `DEEPSEEK_MODEL` | 否 | 复用 `src/agent.py` 的 `MODEL`（`deepseek-chat`） | |
| `PORT` | 否 | `8000` | `run.py` 使用；Render 自动注入 |

## 接口行为

- `GET /api/health`：`{"status":"ok","data_version":"...","champions":N,"items":M,"model":"..."}`；
  数据库不在/读不出时启动直接失败并给中文提示。
- `POST /api/chat`：请求 `{"messages":[{"role":"user","content":"..."}]}`；
  返回 `{"reply": "...", "trace": [...], "model": "..."}`。
  - system prompt 由后端追加，前端传 `role:"system"` 会 422；
  - 超过 20 条消息只保留最新 20 条；
  - `trace` 只含本轮工具调用，按调用顺序；失败项为 `{"tool","args","ok":false,"error"}`；
  - 工具循环最多 6 轮，随后强制模型直接回答；
  - DeepSeek 超时/异常 → 502 `{"detail":"AI 服务暂时不可用，请稍后重试"}`；
  - 同 IP 每分钟 20 次，超出 → 429 `{"detail":"请求太频繁，请稍后再试"}`（内存实现，单实例有效）。
- 日志只记工具名、耗时、状态码；任何日志/响应都不包含 key。
- 限流 IP 取 `X-Forwarded-For` 第一段（Render/Netlify 代理后是真实客户端 IP）；
  直连且伪造该请求头可绕过，free plan 单实例场景可接受。

## 测试（离线，不触网、不花 token）

```bash
cd backend
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  .venv/bin/python -m pytest -q     # 或已激活虚拟环境的 python -m pytest
```

覆盖：health 字段、成功 trace 协议、工具失败 trace、6 轮上限、参数校验 422、
20 条截断、限流 429、上游 502/500 不泄漏。

## Render 部署

根目录 [`render.yaml`](../render.yaml) 已声明：`rootDir: backend`、free plan、
健康检查 `/api/health`、`pip install -r requirements.txt`、
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`。
在 Render 控制台填入 `DEEPSEEK_API_KEY`（不要把 key 写进 yaml），
部署后把 Netlify 的 `/api/*` redirect 指向该服务地址。

## 排障

| 现象 | 原因 / 处理 |
|---|---|
| `Invalid port: ':1]'` | 本机 `NO_PROXY` 含 `[::1]`，命令前加 `NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1` |
| 启动报「未找到数据库」 | 先在仓库根跑 `uv run python src/fetch_data.py`，或用 `LOL_DB_PATH` 指向 db |
| `/api/chat` 恒 502 | 检查 `DEEPSEEK_API_KEY` 是否配置、余额/网络是否正常（日志有状态码） |
| 首次访问慢 | Render free plan 会冷启动，稍等几秒重试即可 |
| 前端跨域失败 | `ALLOWED_ORIGINS` 改成前端域名（逗号分隔），保存后重启服务 |
