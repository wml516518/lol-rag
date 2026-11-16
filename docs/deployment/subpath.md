# 在个人主页域名下独立发布

正式访问地址为 <https://wml.ailoong.net.cn/apps/lol-rag/>，无需单独域名。个人主页只保留入口；LOL RAG 修改后在本仓库 CI/CD，发布不会触碰主页或历史星河目录。

前端目录执行 `npm ci && npm test && npm run build:self-host`，生成 `frontend/dist`。Vite base 为 `/apps/lol-rag/`，API 根据 BASE_URL 请求 `/apps/lol-rag/api/health` 和 `/apps/lol-rag/api/chat`，Nginx 转发到已有 FastAPI 的 `/api/health` 和 `/api/chat`。

`npm run build` 仍可生成根路径构建用于迁移期间的原托管。`npm run build:self-host` 用于新服务器。开发时 `npm run dev -- --base=/apps/lol-rag/`，Vite 代理会移除应用前缀再访问本机 8000 端口。

服务器默认静态位置 `/srv/portfolio/lol-rag/current/`，后端模板监听 `127.0.0.1:8002`。Nginx 共享路由片段维护于个人主页仓库 `deploy/nginx/routes.conf`，真实端口、正式域名和证书必须按实际环境配置。后端已有 API key、SQLite 数据与知识索引需要按原项目部署说明保留；本次不改动后端业务与数据。

GitHub CI 成功后上传 `static-lol-rag` 产物。只有配置变量 `SELF_HOST_DEPLOY_ENABLED=true` 后，成功的 main push 才触发 `deploy-self-host.yml`。需要 Secrets：`DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_SSH_KEY`、`DEPLOY_KNOWN_HOSTS`；Variables：`DEPLOY_ROOT`（默认 `/srv/portfolio`）、`DEPLOY_PORT`（默认 22）。主机公钥必须通过可信渠道确认。

发布使用本仓库 `scripts/deploy-static.sh` 和 `scripts/activate-release.py`，每次创建独立版本并切换 current，保留原版本与旧哈希资源供回滚及已打开页面使用。工具初始版本同步自个人主页的同域名迁移工作，后续修改需在两处分别验证。服务器需要 Python 3 和 tar，静态托管无需安装 Node。

线上已使用 Nginx 子路径静态站、`127.0.0.1:8002` 的 FastAPI 服务和 Cloudflare Tunnel。GitHub 自动发布凭据仍需配置后才能由 CI/CD 代替手工发布。原有 Netlify 恢复与 Smoke workflow 暂保留；正式切换后应在 GitHub Actions 中停用仅针对旧 Netlify 地址的定时工作流，避免继续消耗旧平台额度。
