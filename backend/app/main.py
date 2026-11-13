"""LOL-RAG FastAPI 后端入口。

契约见 docs/api-contract.md 第 5 节：
- GET  /api/health  → 数据版本 + 英雄/装备数量 + 模型名
- POST /api/chat    → {reply, trace, model}；system prompt 由后端追加
- 限流：同 IP 每分钟 20 次（内存实现）
- 上游异常 → 502 中文 detail；日志不出现 key
"""
from __future__ import annotations

import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import settings
from .chat import UPSTREAM_ERROR_DETAIL, UpstreamError, run_chat
from .db_bridge import read_db_stats, validate_db
from .schemas import MAX_MESSAGES, ChatRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("lol_rag.api")

RATE_LIMIT_MAX = 20          # 契约：每分钟 20 次
RATE_LIMIT_WINDOW = 60.0     # 秒
RATE_LIMIT_DETAIL = "请求太频繁，请稍后再试"
UPSTREAM_ERROR_STATUS = 502


class RateLimiter:
    """按 key（IP）的滑动窗口限流。单进程内存实现，Free 计划单实例足够。"""

    def __init__(self, limit: int = RATE_LIMIT_MAX, window: float = RATE_LIMIT_WINDOW):
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            if len(self._hits) > 5000:  # 避免长期运行累积空队列
                self._hits = {k: v for k, v in self._hits.items() if v}
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    """Render/Netlify 代理后取 X-Forwarded-For 第一段，否则用直连 IP。"""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 契约：data/lol.db 不存在时启动失败并给出明确中文提示，不静默降级。
    validate_db()
    if not settings.api_key():
        logger.warning("未配置 DEEPSEEK_API_KEY：/api/chat 将返回 502（本地请写仓库根 .env）")
    logger.info(
        "LOL-RAG API 启动完成 db=%s origins=%s",
        settings.db_path(),
        settings.allowed_origins(),
    )
    yield
    logger.info("LOL-RAG API 关闭")


app = FastAPI(title="LOL-RAG API", version="1.0.0", lifespan=lifespan)

# 不启用 credentials（契约要求），默认允许所有来源便于本地前端联调。
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    """健康检查：Render 用这个路径探活。字段名严格按契约。"""
    try:
        stats = read_db_stats()
    except Exception:
        logger.warning("健康检查读取数据库失败")
        raise HTTPException(status_code=503, detail="数据库暂时不可用")
    return {
        "status": "ok",
        "data_version": stats["data_version"],
        "champions": stats["champions"],
        "items": stats["items"],
        "model": settings.deepseek_model(),
    }


@app.post("/api/chat")
async def chat(payload: ChatRequest, request: Request):
    ip = client_ip(request)
    if not limiter.allow(ip):
        return JSONResponse(status_code=429, content={"detail": RATE_LIMIT_DETAIL})

    # 契约：最多 20 条，超出时截断旧消息（保留最新的 20 条）。
    history = [m.model_dump() for m in payload.messages[-MAX_MESSAGES:]]

    started = time.perf_counter()
    try:
        reply, trace, model = await run_chat(history)
    except UpstreamError:
        logger.warning("chat 失败 status=502 耗时=%.2fs", time.perf_counter() - started)
        return JSONResponse(
            status_code=UPSTREAM_ERROR_STATUS, content={"detail": UPSTREAM_ERROR_DETAIL}
        )
    except Exception as exc:  # 不把堆栈/内部细节返回给前端
        logger.error("chat 未处理异常: %s", type(exc).__name__)
        return JSONResponse(
            status_code=500, content={"detail": "服务器内部错误，请稍后重试"}
        )

    logger.info("chat 完成 status=200 耗时=%.2fs", time.perf_counter() - started)
    return {"reply": reply, "trace": trace, "model": model}
