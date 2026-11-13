"""M1 对话循环 + M3 Agentic 融合：DeepSeek function calling。

M1：协议循环 + 系统提示词 + 工具分发白名单。
M3：新增 search_knowledge 工具，Agent 自己判断该查结构化数据还是检索知识库。
运行:  uv run python src/agent.py   （需要先完成 M0，并构建 M2 索引）
"""
from __future__ import annotations

import json

import httpx

import db
import tools
from config import DB_PATH, deepseek_api_key

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

# 工具的"说明书"：告诉模型有哪些工具、参数长什么样(JSON Schema)
TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "get_champion",
        "description": "按名字查询英雄详情：称号、定位标签、简介、被动(P)以及 "
                       "Q/W/E/R 技能的名称和描述。name 支持中文名（锤石）、"
                       "英文 id（Thresh）或称号（魂锁典狱长）",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string",
                     "description": "英雄名，中文如'锤石'或英文如'Thresh'"}},
            "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "get_item",
        "description": "按名字查询装备详情：价格、效果说明（纯文本）。"
                       "name 支持中文名（无尽之刃）或数字 id（3031）",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "装备名，如'三相之力'"}},
            "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "find_items_by_tag",
        "description": "按类别找装备，按价格升序返回最多 30 件。类别用英文枚举："
                       "Damage/AttackSpeed/Armor/SpellBlock/Health/"
                       "Boots/Mana/CriticalStrike 等",
        "parameters": {"type": "object", "properties": {
            "tag": {"type": "string", "description": "装备类别枚举"}},
            "required": ["tag"]}}},
    # ---- M3 新增：知识库检索（攻略思路类问题走它）
    {"type": "function", "function": {
        "name": "search_knowledge",
        "description": "检索知识库（示例攻略 + 英雄/装备档案文本），适合攻略思路、"
                       "出装推荐、对线/团战打法、阵容应对、英雄对比、新手问题等"
                       "开放性问题；返回最相关的 4 段摘要。"
                       "精确的价格/属性/技能数值请用 get_champion / get_item / "
                       "find_items_by_tag，不要用本工具猜数值",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string",
                      "description": "用一句自然语言描述问题，"
                                     "如'对抗高护甲阵容怎么出装'"}},
            "required": ["query"]}}},
]


def _current_data_version() -> str:
    """从 data/lol.db 的 meta 表读当前数据版本。

    读不到就返回空串而不是抛错：给后端留出"缺少 lol.db 时给出明确中文提示"
    的空间，agent 模块本身只负责提供 prompt。
    """
    try:
        if not DB_PATH.exists():
            return ""
        return db.get_meta_version() or ""
    except Exception:
        return ""


_DATA_VERSION = _current_data_version()
_DATA_VERSION_TEXT = _DATA_VERSION or "未知（数据库尚未初始化）"

# ---------------------------------------------------------------- 填空 ④
# 系统提示词是 Agent 的"岗位说明书"：角色 + 硬性规则。
# 最重要的两条是"必须查工具"和"禁止编造"——LLM 的记忆会过期/幻觉，
# 这正是本项目要先做 M0 数据地基、再让 Agent 通过工具查数据的原因。
# M3 增加了"路由规则"：先判断问题类型，再决定查结构化数据还是检索知识库。
SYSTEM_PROMPT = f"""你是「LOL 助手」，一个熟悉《英雄联盟》的中文数据问答助手。

当前数据版本：{_DATA_VERSION_TEXT}（Data Dragon 中文数据）。

你有一个工具白名单：
- get_champion / get_item / find_items_by_tag：查精确事实（英雄称号/定位/技能、装备价格/效果、按类别找装备）；
- search_knowledge：检索知识库（示例攻略 + 英雄/装备档案），找思路、推荐、打法、对阵建议。

【先判断问题类型，再选工具】
1. 精确事实类问题（某英雄的称号/定位/技能是什么、某装备多少钱/什么效果、有哪些护甲装备）→ 用 get_champion / get_item / find_items_by_tag，不要用 search_knowledge 猜价格或数值。
2. 攻略思路类问题（怎么出装、怎么对线、团战怎么站位、阵容怎么应对、某个英雄怎么打、新手怎么玩、英雄/装备对比）→ 用 search_knowledge 检索相关片段。
3. 需要「事实 + 思路」组合时，先调精确工具拿准数据，再调 search_knowledge 找攻略思路，组合成答案；必要时可以多次调用。同一个问题不要反复用 search_knowledge 换措辞刷结果，一次没检索到要点时再换一次关键词即可。
4. 任何英雄、装备、技能、价格、数值、定位相关事实，都必须以工具返回为准，禁止凭记忆或常识编造。
5. 工具没查到、或数据里没有的内容，直接回答"我的数据里没有"，不要猜测，也不要补充数据里没有的细节。
6. 引用 search_knowledge 的结果时注明来源类型：知识库攻略是「示例攻略（非官方）」；英雄/装备档案来自「Data Dragon 官方数据」。不要把攻略里没有的内容说成攻略内容，也不要把示例攻略当成官方结论。
7. 回答使用简体中文，简洁精炼：先给结论再给细节，适合时用要点列表。
8. 回答中注明数据版本：{_DATA_VERSION_TEXT}。
9. 禁止把工具调用过程写进回答：不要输出函数名、参数 JSON、trace 之类的内部细节，只给最终答案。
10. 用户闲聊或问与数据无关的问题时，正常用中文回答即可。
"""


# ---------------------------------------------------------------- 填空 ⑤
def dispatch(name: str, args: dict):
    """把模型想调的工具名映射到真实函数——安全边界就在这里。

    用字典做白名单：模型只能调用这里登记过的四个函数，其他名字一律抛错。
    注意不能用 eval/getattr 之类的方式动态执行模型给的名字，否则就是
    "模型能执行任意代码"的安全漏洞。
    """
    handlers = {
        "get_champion": tools.get_champion,
        "get_item": tools.get_item,
        "find_items_by_tag": tools.find_items_by_tag,
        "search_knowledge": tools.search_knowledge,
    }
    handler = handlers.get(name)
    if handler is None:
        raise ValueError(f"未知工具 {name}")
    if not isinstance(args, dict):
        raise ValueError(f"工具 {name} 的参数必须是 JSON 对象")

    try:
        return handler(**args)
    except TypeError as e:
        # 参数多了/少了/名字写错时，翻译成模型看得懂的中文提示，方便下一轮改对
        raise ValueError(f"工具 {name} 的参数不合法：{e}") from e


def run(messages: list) -> list:
    """function calling 主循环。协议已写好，读懂它，不用改。"""
    while True:
        resp = httpx.post(
            API_URL,
            headers={"Authorization": f"Bearer {deepseek_api_key()}"},
            json={"model": MODEL, "messages": messages, "tools": TOOL_SPECS},
            timeout=60,
        )
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]

        if msg.get("tool_calls"):
            messages.append(msg)  # 模型的"我要用工具"也要放回历史
            for call in msg["tool_calls"]:
                args = json.loads(call["function"]["arguments"] or "{}")
                try:
                    payload = json.dumps(
                        dispatch(call["function"]["name"], args), ensure_ascii=False
                    )
                except Exception as e:  # 工具报错也回给模型，让它自己调整
                    payload = json.dumps({"error": str(e)}, ensure_ascii=False)
                # tool_call_id 必须与请求一一对应，这是协议要求
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": payload,
                })
            continue  # 带着工具结果再问模型一轮
        messages.append(msg)
        return messages


def _print_tool_trace(new_messages: list) -> None:
    """CLI 调试输出：打印本轮工具调用，检索类工具再打印命中摘要。

    这只是给学习者看的本地调试信息；模型的正式回答里不会包含这些
    （系统提示词第 9 条要求）。
    """
    for msg in new_messages:
        for call in msg.get("tool_calls") or []:
            fn = call["function"]
            print(f"\n[工具] {fn['name']}({fn['arguments']})")

        if msg.get("role") != "tool":
            continue
        try:
            payload = json.loads(msg.get("content") or "")
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("error"):
            print(f"   ↳ 工具返回错误：{payload['error']}")
            continue
        results = payload.get("results")
        if isinstance(results, list) and results:
            summaries = []
            for result in results[:4]:
                if not isinstance(result, dict):
                    continue
                summaries.append(
                    f"{result.get('title', '?')}"
                    f"（{result.get('kind', '?')}，"
                    f"{float(result.get('score', 0) or 0):.2f}）"
                )
            print(f"   ↳ 检索到 {len(results)} 条：" + "；".join(summaries))


def main() -> None:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    print("LOL 助手就绪(命令行版) — 输入 q 退出")
    while True:
        try:
            q = input("\n你> ").strip()
        except EOFError:
            # 支持 `echo "问题" | uv run python src/agent.py` 这种管道冒烟测试
            print()
            break
        if q.lower() in {"q", "quit", "exit"}:
            break
        if not q:
            continue
        messages.append({"role": "user", "content": q})
        start = len(messages)
        messages = run(messages)
        _print_tool_trace(messages[start:])
        print(f"\n助手> {messages[-1]['content']}")


if __name__ == "__main__":
    main()
