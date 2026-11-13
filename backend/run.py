"""启动入口：本地 `python run.py` / `uvicorn app.main:app`。

生产（Render）直接使用 render.yaml 里的 startCommand：
    uvicorn app.main:app --host 0.0.0.0 --port $PORT
"""
from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )
