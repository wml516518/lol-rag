# 仓库约定

LOL-RAG 以子路径形式挂在个人主页域名下：前端 `https://wml.ailoong.net.cn/apps/lol-rag/`，API 同源前缀 `/apps/lol-rag/api/`。静态文件发布到 `/srv/portfolio/static/lol-rag/current`，FastAPI 由 systemd 服务 `lol-rag` 常驻在 `127.0.0.1:8002`。

主机级部署说明在主页仓库的 `docs/deployment/single-domain.md` 与 `docs/deployment/pitfalls.md`（同域名迁移的完整踩坑清单）。

## 硬性约束

- 服务器 `python3` 是 3.6.8。后端运行用 `/srv/portfolio/lol-rag/backend/venv`，任何上传执行的脚本都要兼容 3.6，禁止 `X | None`、`list[str]`、`dict[str, ...]`。
- 只改 `/srv/portfolio/static/lol-rag`；不要碰同机的 home、history、Weekend Merge、movie、nas 的目录与 Nginx 配置。
- `.env` 只在服务器上，权限 `600`、属主 `lolrag`；`lol-rag.service` 用 `EnvironmentFile=` 读取，文件缺失会导致 `Result: resources` 启动失败。
- 前端必须用 `npm run build:self-host`（`--base=/apps/lol-rag/`）；用默认 `build` 会把资源打到根路径，子路径下 404。
- 生产发布只由 `main` push + CI 成功的 `Deploy LOL RAG static site` 触发；它只更新静态站，不会重启或覆盖后端。

## 常见错误

- DeepSeek 模型名大小写敏感：`deepseek-V4-flash` 会 400，允许的是 `deepseek-flash`、`deepseek-v4-pro`；改默认模型要同时确认后端环境变量与前端预设。
- SSE 经 Nginx 代理必须 `proxy_buffering off; proxy_cache off;`，否则前端拿不到流式响应。
- 健康检查是 `/apps/lol-rag/api/health`；`/api/lol-rag/health` 不存在。

## 常用命令

```bash
cd frontend && npm run test && npm run build:self-host
python -m pytest -q                     # backend
FRONTEND_URL=https://wml.ailoong.net.cn/apps/lol-rag \
  API_URL=https://wml.ailoong.net.cn/apps/lol-rag \
  python scripts/smoke_test.py          # 会真实调用模型，注意额度
```

## 历史记录

- 2026-11：从 Netlify + Render 迁到自托管；删除 `netlify-recover.yml`，`smoke.yml` 与 `smoke_test.py` 默认地址改为正式域名，清理 `NETLIFY_AUTH_TOKEN` / `NETLIFY_SITE_ID`。
