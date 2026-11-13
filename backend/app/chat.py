"""带 trace 的 DeepSeek function calling 循环。

设计要点（契约第 4/5 节）：
- prompt / 工具说明书 / 工具分发 / 模型名 / API 地址全部来自 src/agent.py，
  这里只实现「带 trace 的循环」，绝不复制 prompt 或工具逻辑；
- 最多 6 轮工具调用，第 7 次请求不再提供 tools，强制模型收尾；
- 工具异常记录到 trace（ok=false + 中文 error）并把错误回给模型继续；
- 上游异常统一抛 UpstreamError（固定中文消息），由 main.py 转 502，不泄漏堆栈/密钥。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from . import settings
from .db_bridge import get_agent

logger = logging.getLogger("lol_rag.chat")

MAX_TOOL_ROUNDS = 6
UPSTREAM_ERROR_DETAIL = "AI 服务暂时不可用，请稍后重试"
TOOL_ERROR_FALLBACK = "工具调用失败"
EMPTY_REPLY_FALLBACK = "抱歉，我暂时没能组织出答案，请换个问法试试。"


class UpstreamError(Exception):
    """DeepSeek 上游不可用。对外只暴露固定中文提示。"""


def _model_name() -> str:
    # settings.deepseek_model() 优先读环境变量，否则回落到 src/agent.py 的 MODEL。
    return settings.deepseek_model()


def _assistant_message(msg: dict) -> dict:
    """把上游 message 收敛成回传历史所需的最小字段（附带 tool_calls）。"""
    out: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
    calls = msg.get("tool_calls") or []
    if calls:
        out["tool_calls"] = [
            {
                "id": call.get("id"),
                "type": "function",
                "function": {
                    "name": (call.get("function") or {}).get("name", ""),
                    "arguments": (call.get("function") or {}).get("arguments") or "{}",
                },
            }
            for call in calls
        ]
    return out


def _parse_arguments(raw: Any) -> dict:
    """解析模型给的 JSON 参数；非法时抛 ValueError（会被记成失败 trace）。"""
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        return raw
    args = json.loads(raw)
    if not isinstance(args, dict):
        raise ValueError("工具参数必须是 JSON 对象")
    return args


def _json_safe(value: Any) -> Any:
    """保证 trace 里的 result 一定能被 FastAPI 序列化。"""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)


async def _call_deepseek(
    messages: list[dict], tools: list[dict] | None, model: str
) -> dict:
    """向 DeepSeek 发一次 chat/completions 请求。

    单独抽成函数，测试里 monkeypatch 它即可完全离线；真实链路见 main.py。
    """
    agent = get_agent()
    api_key = settings.api_key()
    if not api_key:
        # 配错/漏配都是「AI 服务不可用」，日志只记原因类型，绝不打印 key。
        logger.warning("chat 未配置 DEEPSEEK_API_KEY")
        raise UpstreamError()

    payload: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        payload["tools"] = tools

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                agent.API_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
    except httpx.HTTPError as exc:  # 超时 / 连接失败 / 代理异常
        logger.warning("chat 上游请求失败: %s", type(exc).__name__)
        raise UpstreamError() from None

    if resp.status_code != 200:
        logger.warning("chat 上游返回状态码 %s", resp.status_code)
        raise UpstreamError()

    try:
        data = resp.json()
        data["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        logger.warning("chat 上游响应格式异常")
        raise UpstreamError() from None
    return data


def _trace_failure(trace: list[dict], name: str, args: dict, error: str) -> dict:
    message = (error.strip() or TOOL_ERROR_FALLBACK)[:500]
    trace.append({"tool": name, "args": args, "ok": False, "error": message})
    return {"error": message}


def _run_tool_call(agent, call: dict, trace: list[dict]) -> dict:
    """执行一次工具调用：返回要回填给模型的 payload（含错误）。"""
    function = call.get("function") or {}
    name = function.get("name") or ""
    raw_args = function.get("arguments")

    try:
        if not name:
            raise ValueError(TOOL_ERROR_FALLBACK)
        args = _parse_arguments(raw_args)
    except Exception as exc:  # 参数不合法：记失败 trace，错误回给模型
        return _trace_failure(trace, name, _safe_args(raw_args), str(exc))

    try:
        result = agent.dispatch(name, args)
    except Exception as exc:  # 工具报错也回给模型，让它自己调整
        return _trace_failure(trace, name, args, str(exc))

    safe_result = _json_safe(result)
    trace.append({"tool": name, "args": args, "ok": True, "result": safe_result})
    return safe_result


def _safe_args(raw_args: Any) -> dict:
    """解析失败时尽量给出 args 的原始形态（契约要求 trace 里有 args 字段）。"""
    if isinstance(raw_args, dict):
        return raw_args
    try:
        parsed = json.loads(raw_args) if isinstance(raw_args, str) else None
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


async def run_chat(history: list[dict]) -> tuple[str, list[dict], str]:
    """执行一次对话，返回 (reply, trace, model)。

    history 只包含 user/assistant（校验与截断在 main.py 做）；system prompt 由这里追加。
    """
    agent = get_agent()
    model = _model_name()
    messages: list[dict] = [
        {"role": "system", "content": agent.SYSTEM_PROMPT},
        *({"role": item["role"], "content": item["content"]} for item in history),
    ]
    tools = list(agent.TOOL_SPECS)
    trace: list[dict] = []
    started = time.perf_counter()

    # 最多 6 轮「模型要工具 → 执行 → 回填」；每轮模型可能一次调多个工具。
    for _ in range(MAX_TOOL_ROUNDS):
        data = await _call_deepseek(messages, tools, model)
        msg = data["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            reply = msg.get("content") or EMPTY_REPLY_FALLBACK
            break
        messages.append(_assistant_message(msg))
        for call in calls:
            payload = _run_tool_call(agent, call, trace)
            messages.append({
                "role": "tool",
                "tool_call_id": call.get("id"),
                "content": json.dumps(payload, ensure_ascii=False),
            })
    else:
        # 6 轮工具都还没收尾：第 7 次不再给 tools，强制模型直接回答。
        logger.info("chat 达到工具轮次上限，强制收尾")
        data = await _call_deepseek(messages, None, model)
        reply = data["choices"][0]["message"].get("content") or EMPTY_REPLY_FALLBACK

    logger.info(
        "chat 完成 tools=%s trace_len=%s elapsed=%.2fs",
        [item["tool"] for item in trace],
        len(trace),
        time.perf_counter() - started,
    )
    return reply, trace, model
