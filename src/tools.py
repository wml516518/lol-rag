"""M1：给 Agent 用的"工具"——本质就是普通 Python 函数。

Agent 只能调用你在 dispatch 里注册的函数，这就是防止它乱来的白名单。

填空 ③：三个函数已实现。数据通过 db.query_champions()/query_items() 拿；
name 匹配顺序严格按契约：精确优先 → LIKE '%name%' 模糊兜底；
失败一律 raise ValueError("中文说明")，Agent 循环会捕获它并把错误回给模型，
让模型自己换个名字再试（而不是整个程序崩掉）。
"""
from __future__ import annotations

import db

# 数据源里 tags 是英文枚举，这里备了张中英对照表(M1 直接用，不用自己编)
TAG_CN2EN = {
    "输出": "Damage", "物理输出": "Damage", "法强": "SpellDamage",
    "攻速": "AttackSpeed", "暴击": "CriticalStrike", "吸血": "LifeSteal",
    "护甲": "Armor", "魔抗": "SpellBlock", "生命": "Health",
    "回血": "HealthRegen", "法力": "Mana", "回蓝": "ManaRegen",
    "鞋子": "Boots", "减CD": "CooldownReduction", "技能急速": "CooldownReduction",
}


def _clean(value: str) -> str:
    """去掉首尾空格；空串在这里统一处理，避免所有查询函数都写一遍。"""
    return (value or "").strip()


def _champion_payload(champ: dict) -> dict:
    """把数据库行整理成契约第 3 节约定的返回结构。"""
    return {
        "id": champ["id"],
        "name": champ["name"],
        "title": champ["title"],
        "tags": champ.get("tags", []),
        "blurb": champ.get("blurb", ""),
        "spells": champ.get("spells", []),
    }


def _item_payload(item: dict) -> dict:
    """装备返回结构里不含 description 之外的内部字段。"""
    return {
        "id": item["id"],
        "name": item["name"],
        "gold": item.get("gold", 0),
        "plaintext": item.get("plaintext", ""),
        "description": item.get("description", ""),
        "tags": item.get("tags", []),
    }


def get_champion(name: str) -> dict:
    """按名字查英雄详情(称号/定位/简介/被动与技能)。填空 ③-a 已实现"""
    query = _clean(name)
    if not query:
        raise ValueError("英雄名不能为空，请给出中文名（如 锤石）或英文 id（如 Thresh）")

    champions = db.query_champions()
    query_lower = query.lower()

    # 1) 英文 id 精确匹配(忽略大小写)：Thresh / thresh / THRESH 都命中
    for champ in champions:
        if champ["id"].lower() == query_lower:
            return _champion_payload(champ)

    # 2) 中文名精确匹配：锤石
    for champ in champions:
        if champ["name"] == query:
            return _champion_payload(champ)

    # 3) 称号精确匹配：魂锁典狱长 / 疾风剑豪
    for champ in champions:
        if champ["title"] == query:
            return _champion_payload(champ)

    # 4) 兜底：name/title 模糊包含，等价于契约里的 LIKE '%name%'。
    #    模糊命中可能有多条：优先"名字里包含"、其次"名字以它开头"、再短的名字，
    #    这样 get_champion("锤") 会先命中锤石，而不是称号里带"锤"的波比。
    matches = [
        champ for champ in champions
        if query_lower in champ["name"].lower()
        or query_lower in champ["title"].lower()
    ]
    if matches:
        matches.sort(key=lambda champ: (
            query_lower not in champ["name"].lower(),
            not champ["name"].lower().startswith(query_lower),
            len(champ["name"]),
            champ["id"],
        ))
        return _champion_payload(matches[0])

    raise ValueError(f"未找到英雄 {query}")


def get_item(name: str) -> dict:
    """按名字查装备详情(价格/效果)。填空 ③-b 已实现"""
    query = _clean(name)
    if not query:
        raise ValueError("装备名不能为空，请给出中文名（如 无尽之刃）或数字 id（如 3031）")

    items = db.query_items()

    # 1) id 精确匹配：用户/模型可能直接给 3031 这种数字 id
    if query.isdigit():
        for item in items:
            if item["id"] == int(query):
                return _item_payload(item)

    # 2) 中文名精确匹配：无尽之刃
    for item in items:
        if item["name"] == query:
            return _item_payload(item)

    # 3) 兜底：name 模糊包含（LIKE '%name%'），多条时优先最短名字
    query_lower = query.lower()
    matches = [item for item in items if query_lower in item["name"].lower()]
    if matches:
        matches.sort(key=lambda item: (len(item["name"]), item["id"]))
        return _item_payload(matches[0])

    raise ValueError(f"未找到装备 {query}")


def find_items_by_tag(tag: str) -> list[dict]:
    """按类别找装备。tag 支持中文(先经 TAG_CN2EN 转英文)。填空 ③-c 已实现"""
    query = _clean(tag)
    if not query:
        raise ValueError("装备类别不能为空，可用中文类别如：护甲、魔抗、生命、法力、攻速、暴击、吸血、鞋子")

    # 中文 → 英文枚举（已经是英文的保持原样，下面统一忽略大小写匹配）
    english = TAG_CN2EN.get(query, query)

    items = db.query_items()
    # 合法类别 = 对照表里的英文枚举 ∪ 数据库里真实出现过的 tags，
    # 这样既能拦住乱编的类别，又不会因为新版本新增 tag 而误报。
    known_tags = {value.lower() for value in TAG_CN2EN.values()}
    for item in items:
        known_tags.update(t.lower() for t in item.get("tags", []))
    if english.lower() not in known_tags:
        raise ValueError(
            f"未知装备类别 {query}，可用中文类别如：护甲、魔抗、生命、法力、"
            "攻速、暴击、吸血、鞋子、技能急速"
        )

    matched = [
        item for item in items
        if any(t.lower() == english.lower() for t in item.get("tags", []))
    ]
    # 契约要求：按价格升序，最多 30 件（同价按 id 排，保证结果稳定）
    matched.sort(key=lambda item: (item.get("gold", 0), item["id"]))
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "gold": item.get("gold", 0),
            "plaintext": item.get("plaintext", ""),
            "tags": item.get("tags", []),
        }
        for item in matched[:30]
    ]


def search_knowledge(query: str) -> dict:
    """M3：检索知识库（示例攻略 + 英雄/装备档案），返回最相关的 4 段文本。

    和前面三个「精确事实」工具不同：这里回答的是攻略思路、出装推荐、对线团战、
    阵容应对这类开放问题。检索实现在 src/retrieve.py（M2 检索层），
    本函数只是给 Agent 提供一个受控入口（白名单工具）。
    """
    clean = _clean(query)
    if not clean:
        raise ValueError("检索内容不能为空，请用一句话描述你想了解的问题")

    try:
        # 延迟导入：只有第一次调用本工具时才加载 numpy、构建/加载索引。
        # 这样 Agent 启动、以及只查价格的请求都不必为 M2 付启动成本。
        import retrieve
        results = retrieve.search(clean, k=4)
    except ValueError:
        raise  # 检索层的参数错误（例如空查询）原样透传中文说明
    except Exception as e:
        # 索引/依赖异常也翻译成中文给模型看，避免把内部堆栈泄漏到回答里
        raise ValueError(f"知识库检索失败：{e}") from e

    return {
        "query": clean,
        "results": [
            {
                "title": item["title"],
                "kind": item["kind"],
                "snippet": item["snippet"],
                "score": item["score"],
            }
            for item in results
        ],
    }
