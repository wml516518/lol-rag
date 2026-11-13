"""复用仓库根 src/ 的桥梁。

契约要求后端不复制 prompt / 工具逻辑，所以：
- 把 <repo>/src 插到 sys.path 最前面，再 import agent（TOOL_SPECS / SYSTEM_PROMPT /
  dispatch / MODEL / API_URL 全部来自 src）；
- 路径全部基于 __file__ 推导，Render rootDir=backend 时同样成立
  （backend/app/db_bridge.py -> parents[2] == 仓库根）。
- 数据库本身用 sqlite3 只读几个聚合值，不依赖 src/db.py 的私有实现。
"""
from __future__ import annotations

import importlib
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .settings import REPO_ROOT, db_path

SRC_DIR = REPO_ROOT / "src"

_agent_module = None


def ensure_src_on_path() -> Path:
    src = str(SRC_DIR)
    if src not in sys.path:
        sys.path.insert(0, src)
    return SRC_DIR


def get_agent():
    """导入并缓存 src/agent.py；顺带让 LOL_DB_PATH 覆盖对 src 的 db 生效。"""
    global _agent_module
    if _agent_module is not None:
        return _agent_module

    ensure_src_on_path()
    # 先导入 config 并应用 DB 路径覆盖，再导入 db/tools/agent：
    # src/db.py 在 import 时就把 config.DB_PATH 绑成了模块级常量。
    src_config = importlib.import_module("config")
    wanted = db_path()
    if Path(getattr(src_config, "DB_PATH")) != wanted:
        # 先改 config，再导入 db：src/db.py 里是 `from config import DB_PATH`。
        src_config.DB_PATH = wanted
        db_module = importlib.import_module("db")
        db_module.DB_PATH = wanted

    _agent_module = importlib.import_module("agent")
    return _agent_module


def validate_db() -> None:
    """启动自检：数据库不存在 / 读不出表就明确报错，不静默降级。"""
    path = db_path()
    if not path.exists():
        raise RuntimeError(
            f"未找到数据库 {path}：请先在仓库根执行 "
            "`uv run python src/fetch_data.py` 生成 data/lol.db，"
            "或用 LOL_DB_PATH 指向已有数据库。"
        )
    try:
        stats = read_db_stats()
    except sqlite3.Error as exc:
        raise RuntimeError(
            f"数据库 {path} 无法读取（{type(exc).__name__}）："
            "请重新执行 `uv run python src/fetch_data.py` 生成。"
        ) from None
    if stats["champions"] <= 0 or stats["items"] <= 0:
        raise RuntimeError(
            f"数据库 {path} 里没有英雄/装备数据："
            "请重新执行 `uv run python src/fetch_data.py` 生成。"
        )


def read_db_stats() -> dict[str, Any]:
    """给 /api/health 用的只读聚合查询（每次现读，容忍数据被刷新）。"""
    path = db_path()
    with sqlite3.connect(path, timeout=5) as conn:
        version_row = conn.execute(
            "SELECT value FROM meta WHERE key='version'"
        ).fetchone()
        champions = conn.execute("SELECT COUNT(*) FROM champions").fetchone()[0]
        items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    return {
        "data_version": version_row[0] if version_row else None,
        "champions": int(champions),
        "items": int(items),
    }
