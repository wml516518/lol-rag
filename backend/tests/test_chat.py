"""/api/chat 协议测试：成功 trace、工具失败 trace、轮次上限、上游 502。

全部用假 LLM + 假 dispatch，不触网；trace 字段名严格按 docs/api-contract.md 第 5 节。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import chat

CHAMPION_RESULT = {
    "id": "Thresh",
    "name": "锤石",
    "title": "魂锁典狱长",
    "tags": ["Support", "Tank"],
    "blurb": "魂锁典狱长是一位支援型坦克……",
    "spells": [{"key": "Q", "name": "死亡判决", "description": "钩住敌人"}],
}


def make_tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def chat_request(content: str = "锤石的Q技能是什么？") -> dict:
    return {"messages": [{"role": "user", "content": content}]}


def test_chat_success_returns_reply_trace_model(client, fake_llm, fake_dispatch, agent_module):
    fake_dispatch.result = CHAMPION_RESULT
    fake_llm.queue_tool_calls([make_tool_call("get_champion", {"name": "锤石"})])
    fake_llm.queue_content("锤石的Q是「死亡判决」。")

    response = client.post("/api/chat", json=chat_request())
    assert response.status_code == 200
    body = response.json()

    assert set(body) == {"reply", "trace", "model"}
    assert body["reply"] == "锤石的Q是「死亡判决」。"
    assert body["model"] == "deepseek-chat"
    assert body["trace"] == [
        {
            "tool": "get_champion",
            "args": {"name": "锤石"},
            "ok": True,
            "result": CHAMPION_RESULT,
        }
    ]

    # 第 1 次上游请求：system prompt 由后端追加，工具说明复用 src/agent.py
    first = fake_llm.calls[0]
    assert first["messages"][0] == {
        "role": "system",
        "content": agent_module.SYSTEM_PROMPT,
    }
    assert first["messages"][1] == {"role": "user", "content": "锤石的Q技能是什么？"}
    assert first["tools"] == agent_module.TOOL_SPECS

    # 第 2 次上游请求：assistant(tool_calls) + tool 结果按协议回填
    second_messages = fake_llm.calls[1]["messages"]
    assert [m["role"] for m in second_messages] == ["system", "user", "assistant", "tool"]
    assert second_messages[2]["tool_calls"][0]["id"] == "call_1"
    assert second_messages[3]["tool_call_id"] == "call_1"
    assert json.loads(second_messages[3]["content"]) == CHAMPION_RESULT
    # dispatch 只被真实调用一次，参数来自模型
    assert fake_dispatch.calls == [("get_champion", {"name": "锤石"})]


def test_chat_tool_error_recorded_and_fed_back(client, fake_llm, fake_dispatch):
    fake_dispatch.error = ValueError("未找到英雄 不存在的英雄")
    fake_llm.queue_tool_calls(
        [make_tool_call("get_champion", {"name": "不存在的英雄"})]
    )
    fake_llm.queue_content("我的数据里没有这个英雄。")

    response = client.post("/api/chat", json=chat_request("不存在的英雄是谁？"))
    assert response.status_code == 200
    body = response.json()

    assert body["reply"] == "我的数据里没有这个英雄。"
    assert body["trace"] == [
        {
            "tool": "get_champion",
            "args": {"name": "不存在的英雄"},
            "ok": False,
            "error": "未找到英雄 不存在的英雄",
        }
    ]
    # 失败结果以 {"error": ...} 回给模型
    tool_message = fake_llm.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool"
    assert json.loads(tool_message["content"]) == {"error": "未找到英雄 不存在的英雄"}


def test_chat_caps_tool_rounds_at_six(client, fake_llm, fake_dispatch):
    for i in range(chat.MAX_TOOL_ROUNDS):
        fake_llm.queue_tool_calls(
            [make_tool_call("get_champion", {"name": f"英雄{i}"}, call_id=f"call_{i}")]
        )
    fake_llm.queue_content("最终回答")

    response = client.post("/api/chat", json=chat_request())
    assert response.status_code == 200
    body = response.json()

    assert body["reply"] == "最终回答"
    assert len(body["trace"]) == chat.MAX_TOOL_ROUNDS
    assert len(fake_dispatch.calls) == chat.MAX_TOOL_ROUNDS
    # 6 轮工具调用 + 最后 1 次强制收尾（不再带 tools）
    assert len(fake_llm.calls) == chat.MAX_TOOL_ROUNDS + 1
    assert not fake_llm.calls[-1]["tools"]


def test_upstream_error_maps_to_502(client, fake_llm):
    fake_llm.queue_error(chat.UpstreamError())
    response = client.post("/api/chat", json=chat_request())

    assert response.status_code == 502
    assert response.json() == {"detail": "AI 服务暂时不可用，请稍后重试"}
    text = response.text.lower()
    assert "traceback" not in text and "sk-" not in text


def test_unexpected_error_is_500_without_leak(client, fake_llm):
    fake_llm.queue_error(RuntimeError("内部细节不该外泄"))
    response = client.post("/api/chat", json=chat_request())

    assert response.status_code == 500
    assert "内部细节不该外泄" not in response.text
    assert "traceback" not in response.text.lower()


def test_missing_api_key_is_502(monkeypatch, agent_module):
    """没配 key 也不能打出网络请求，直接按上游不可用处理。"""
    monkeypatch.setattr(chat.settings, "api_key", lambda: "")

    with pytest.raises(chat.UpstreamError):
        asyncio.run(chat._call_deepseek([], None, "deepseek-chat"))


def test_call_deepseek_maps_network_error(monkeypatch):
    class BoomClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(chat.httpx, "AsyncClient", BoomClient)
    with pytest.raises(chat.UpstreamError):
        asyncio.run(chat._call_deepseek([{"role": "user", "content": "hi"}], None, "deepseek-chat"))
