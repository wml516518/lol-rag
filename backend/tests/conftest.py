"""pytest 公共夹具：完全离线的后端测试。

策略：
- 先把 backend/ 放进 sys.path，再设置测试用环境变量，最后 import app（settings 在 import 时加载）；
- 测试 key 是假值，且所有 LLM 调用都被 monkeypatch 掉，绝不触网；
- 每个用例前重置内存限流器，避免互相影响。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("DEEPSEEK_API_KEY", "test-key-not-real")
os.environ.setdefault("LOL_DB_PATH", str(REPO_ROOT / "data" / "lol.db"))
os.environ.setdefault("ALLOWED_ORIGINS", "*")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import chat  # noqa: E402
from app.db_bridge import get_agent  # noqa: E402
from app.main import app, limiter  # noqa: E402


class FakeLLM:
    """按脚本吐 responses 的假 DeepSeek。"""

    def __init__(self) -> None:
        self.script: list = []
        self.calls: list[dict] = []

    # ---- 脚本编排 ----
    def queue_content(self, text: str):
        self.script.append({"role": "assistant", "content": text})
        return self

    def queue_tool_calls(self, calls: list[dict]):
        self.script.append({"role": "assistant", "content": None, "tool_calls": calls})
        return self

    def queue_error(self, exc: BaseException):
        self.script.append(exc)
        return self

    # ---- 被 chat 调用 ----
    async def __call__(self, messages, tools, model):
        # 快照：断言请求历史，而不是引用被后续 append 修改的列表
        self.calls.append(
            {
                "messages": [dict(item) for item in messages],
                "tools": tools,
                "model": model,
            }
        )
        if not self.script:
            raise AssertionError("FakeLLM 脚本已用完，用例少排了一个响应")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return {"choices": [{"message": item}]}


CHAMPION_RESULT = {
    "id": "Thresh",
    "name": "锤石",
    "title": "魂锁典狱长",
    "tags": ["Support", "Tank"],
    "blurb": "魂锁典狱长是一位支援型坦克……",
    "spells": [{"key": "Q", "name": "死亡判决", "description": "钩住敌人"}],
}


class RecordingDispatch:
    """替代 src/agent.dispatch，记录调用并可注入成功结果/异常。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.result = CHAMPION_RESULT
        self.error: BaseException | None = None

    def __call__(self, name: str, args: dict):
        self.calls.append((name, args))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(chat, "_call_deepseek", fake)
    return fake


@pytest.fixture
def fake_dispatch(monkeypatch):
    recording = RecordingDispatch()
    monkeypatch.setattr(get_agent(), "dispatch", recording)
    return recording


@pytest.fixture
def agent_module():
    return get_agent()
