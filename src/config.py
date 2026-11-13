"""全局配置：路径、Data Dragon 地址、.env 读取。

这个文件不需要改，但要读懂——你以后每个项目都有一个类似的家伙。
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "lol.db"

DD_BASE = "https://ddragon.leagueoflegends.com"


def load_dotenv(path: Path | None = None) -> None:
    """极简 .env 加载器：把 KEY=VALUE 写进环境变量（不引入第三方依赖）。"""
    env_file = path or PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def deepseek_api_key() -> str:
    load_dotenv()
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise RuntimeError(
            "未找到 DEEPSEEK_API_KEY：请把 .env.example 复制为 .env 并填入 key"
        )
    return key
