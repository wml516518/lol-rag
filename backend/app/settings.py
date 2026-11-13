"""后端配置：环境变量读取 + 本地开发时自动加载仓库根 .env。

约定（见 docs/api-contract.md 第 5 节）：
- DEEPSEEK_API_KEY  必填，只在 Render 环境变量里配置，禁止提交；本地从仓库根 .env 读
- ALLOWED_ORIGINS   逗号分隔，默认 *
- LOL_DB_PATH       可选，默认 <repo>/data/lol.db
- DEEPSEEK_MODEL    可选，默认复用 src/agent.py 的 MODEL（deepseek-chat）

注意：本模块只保存/读取配置，绝不打印 key。
"""
from __future__ import annotations

import os
from pathlib import Path

# backend/app/settings.py -> parents[0]=app, [1]=backend, [2]=仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "lol.db"
DEFAULT_MODEL = "deepseek-chat"

_ENV_LOADED = False


def _load_dotenv(path: Path | None = None) -> None:
    """极简 .env 加载器（与 src/config.py 同款思路，不引入第三方依赖）。

    只用 setdefault：真实环境变量（如 Render 后台）始终优先于本地文件。
    """
    env_file = path or REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def load_env(force: bool = False) -> None:
    global _ENV_LOADED
    if _ENV_LOADED and not force:
        return
    _load_dotenv()
    _ENV_LOADED = True


# 模块导入即加载一次：uvicorn 启动、测试 import 都能拿到 .env 里的配置。
load_env()


def api_key() -> str:
    """返回 DeepSeek key；没有则返回空串（调用方给中文错误，绝不回显 key）。"""
    load_env()
    return os.environ.get("DEEPSEEK_API_KEY", "").strip()


def allowed_origins() -> list[str]:
    """ALLOWED_ORIGINS：逗号分隔，默认 ["*"]。"""
    load_env()
    raw = os.environ.get("ALLOWED_ORIGINS", "*")
    origins = [x.strip().rstrip("/") for x in raw.split(",") if x.strip()]
    return origins or ["*"]


def db_path() -> Path:
    """LOL_DB_PATH 或默认 <repo>/data/lol.db；相对路径以仓库根为基准。"""
    load_env()
    raw = os.environ.get("LOL_DB_PATH", "").strip()
    if not raw:
        return DEFAULT_DB_PATH
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path)


def deepseek_model() -> str:
    """DEEPSEEK_MODEL 优先；否则复用 src/agent.py 的 MODEL（默认 deepseek-chat）。"""
    load_env()
    override = os.environ.get("DEEPSEEK_MODEL", "").strip()
    if override:
        return override
    try:
        # 延迟导入避免循环依赖，同时保证「模型名只认 src 一处」。
        from .db_bridge import get_agent

        return get_agent().MODEL or DEFAULT_MODEL
    except Exception:
        return DEFAULT_MODEL
