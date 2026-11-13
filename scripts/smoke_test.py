"""线上冒烟自检：验证 Netlify 前端 + Render 后端 + Agent 工具链路。

用法：
    FRONTEND_URL=https://lol-rag.netlify.app \
    API_URL=https://lol-rag-api.onrender.com \
    uv run python scripts/smoke_test.py

不传环境变量时使用上述默认值。退出码非 0 表示有检查失败。
Render free plan 会冷启动，单次请求可能 40-90 秒，超时设置得比较宽。
"""
from __future__ import annotations

import json
import os
import sys
import time

# 本机 NO_PROXY 里可能残留 [::1]，httpx 解析会报 Invalid port。
# 在 import httpx 之前把它覆盖成安全值（线上 CI 没有这个变量）。
for key in ("NO_PROXY", "no_proxy"):
    os.environ[key] = "localhost,127.0.0.1"

import httpx  # noqa: E402

FRONTEND_URL = os.environ.get("FRONTEND_URL", "https://lol-rag.netlify.app").rstrip("/")
API_URL = os.environ.get("API_URL", "https://lol-rag-api.onrender.com").rstrip("/")
TIMEOUT = float(os.environ.get("SMOKE_TIMEOUT", "120"))

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  — ' + detail) if detail else ''}")


def post_chat(base: str, question: str) -> dict:
    started = time.time()
    resp = httpx.post(
        f"{base}/api/chat",
        json={"messages": [{"role": "user", "content": question}]},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    elapsed = time.time() - started
    print(f"      「{question}」 {elapsed:.1f}s")
    return resp.json()


def main() -> int:
    print(f"frontend: {FRONTEND_URL}\napi:      {API_URL}\n")

    # 1. 前端页面可访问
    try:
        home = httpx.get(FRONTEND_URL, timeout=60, follow_redirects=True)
        check("前端首页 200", home.status_code == 200, f"status={home.status_code}")
        check("前端标题包含 LOL-RAG", "LOL-RAG" in home.text, home.text[:80])
    except Exception as exc:  # noqa: BLE001
        check("前端首页 200", False, repr(exc))

    # 2. 同源代理 + 后端健康检查
    for label, base in (("Netlify 代理", FRONTEND_URL), ("Render 直连", API_URL)):
        try:
            health = httpx.get(f"{base}/api/health", timeout=TIMEOUT).json()
            required = {"status", "data_version", "champions", "items", "model"}
            ok = health.get("status") == "ok" and required <= set(health)
            check(
                f"{label} /api/health",
                ok,
                f"{health.get('data_version')} / 英雄 {health.get('champions')} / 装备 {health.get('items')}",
            )
        except Exception as exc:  # noqa: BLE001
            check(f"{label} /api/health", False, repr(exc))

    # 3. 精确事实问题 → 应该走 get_champion / get_item
    try:
        data = post_chat(FRONTEND_URL, "锤石的Q技能是什么？")
        tools = [item.get("tool") for item in data.get("trace", [])]
        check(
            "精确事实走结构化工具",
            "get_champion" in tools and bool(data.get("reply")),
            f"trace={tools}",
        )
    except Exception as exc:  # noqa: BLE001
        check("精确事实走结构化工具", False, repr(exc))

    # 4. 攻略型问题 → 应该走 search_knowledge（M3 路由）
    try:
        data = post_chat(FRONTEND_URL, "对抗高护甲阵容该出什么装备？")
        tools = [item.get("tool") for item in data.get("trace", [])]
        check(
            "攻略问题走知识库检索",
            "search_knowledge" in tools and bool(data.get("reply")),
            f"trace={tools}",
        )
    except Exception as exc:  # noqa: BLE001
        check("攻略问题走知识库检索", False, repr(exc))

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 项通过")
    if failed:
        print("失败项：" + "、".join(failed))
        return 1
    print("线上冒烟全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
