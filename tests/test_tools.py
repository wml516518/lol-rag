"""M1 工具函数单元测试（pytest）。

测试策略：
- 大部分用例用 tmp_path 里现造的「迷你 SQLite」，通过 monkeypatch 把
  db.DB_PATH 指过去。数据完全可控，不会因为 Data Dragon 版本更新而挂；
- 最后几条 test_real_db_* 会读仓库真实的 data/lol.db 做冒烟（文件不存在就 skip），
  确认 fetch_data.py 的产物真的能被工具读到、版本号/技能/描述都在。

运行（注意本机代理坑）：
    uv run python -m pytest tests/test_tools.py -q
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# src/ 不是一个 package，测试里手动加进 sys.path，和 agent.py 的 import 方式一致
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import db  # noqa: E402
import tools  # noqa: E402

REAL_DB_PATH = SRC_DIR.parent / "data" / "lol.db"

# ---------------------------------------------------------------- 测试夹具
# 只放三条英雄 / 五件装备，字段与 fetch_data.py 归一化后的结构完全一致。
CHAMPIONS = [
    {
        "id": "Thresh", "name": "锤石", "title": "魂锁典狱长",
        "tags": ["Support", "Tank"], "blurb": "魂锁典狱长锤石是……",
        "spells": [
            {"key": "P", "name": "地狱诅咒", "description": "收割附近敌人的灵魂。"},
            {"key": "Q", "name": "死亡判决", "description": "绑住敌方目标并拉向自己。"},
            {"key": "W", "name": "魂引之灯", "description": "扔出灯笼为友军提供护盾。"},
            {"key": "E", "name": "厄运钟摆", "description": "挥舞锁链把敌人带向指定方向。"},
            {"key": "R", "name": "幽冥监牢", "description": "构建一座伤害并减速敌人的监牢。"},
        ],
    },
    {
        "id": "Yasuo", "name": "亚索", "title": "疾风剑豪",
        "tags": ["Fighter", "Assassin"], "blurb": "疾风剑豪亚索是……",
        "spells": [
            {"key": "P", "name": "浪客之道", "description": "被动护盾与暴击加成。"},
            {"key": "Q", "name": "斩钢闪", "description": "向前突刺。"},
            {"key": "W", "name": "风之障壁", "description": "生成风墙。"},
            {"key": "E", "name": "踏前斩", "description": "冲向敌人。"},
            {"key": "R", "name": "狂风绝息斩", "description": "对击飞的敌人造成伤害。"},
        ],
    },
    {
        "id": "Poppy", "name": "波比", "title": "圣锤之毅",
        "tags": ["Fighter", "Tank"], "blurb": "圣锤之毅波比是……",
        "spells": [
            {"key": "P", "name": "钢铁大使", "description": "投掷盾牌。"},
            {"key": "Q", "name": "圣锤猛击", "description": "砸地造成伤害。"},
            {"key": "W", "name": "坚定风采", "description": "阻止突进。"},
            {"key": "E", "name": "英勇冲撞", "description": "把敌人撞向地形。"},
            {"key": "R", "name": "持卫的裁决", "description": "把敌人击飞。"},
        ],
    },
]

ITEMS = [
    {"id": 3031, "name": "无尽之刃", "gold": 3500, "plaintext": "巨幅加强暴击",
     "description": "75 攻击力 25% 暴击几率 30% 暴击伤害",
     "tags": ["CriticalStrike", "Damage"]},
    {"id": 3078, "name": "三相之力", "gold": 3333, "plaintext": "成吨的伤害",
     "description": "36 攻击力 30% 攻击速度 333 生命值",
     "tags": ["Health", "Damage", "AttackSpeed", "CooldownReduction"]},
    {"id": 3047, "name": "布甲", "gold": 300, "plaintext": "略微提升护甲",
     "description": "15 护甲", "tags": ["Armor"]},
    {"id": 3110, "name": "冰霜之心", "gold": 2500, "plaintext": "巨幅提升护甲并减慢敌人的普攻",
     "description": "75 护甲 400 法力", "tags": ["Armor", "Mana"]},
    {"id": 3006, "name": "狂战士胫甲", "gold": 1100, "plaintext": "提升攻速和移速",
     "description": "25% 攻击速度 45 移动速度", "tags": ["Boots", "AttackSpeed"]},
]


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """把 db.DB_PATH 指向临时库并写入固定测试数据，跑完自动还原。"""
    db_path = tmp_path / "test_lol.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()
    db.save_champions("99.9.9", CHAMPIONS)
    db.save_items("99.9.9", ITEMS)
    return db_path


@pytest.fixture()
def real_db(monkeypatch):
    """真实 data/lol.db；不存在或没初始化就 skip，保证测试不依赖网络。"""
    if not REAL_DB_PATH.exists():
        pytest.skip("data/lol.db 不存在，先运行 src/fetch_data.py")
    monkeypatch.setattr(db, "DB_PATH", REAL_DB_PATH)
    try:
        version = db.get_meta_version()
    except Exception:
        pytest.skip("data/lol.db 还没有初始化，先运行 src/fetch_data.py")
    if not version:
        pytest.skip("data/lol.db 缺少 meta.version，先运行 src/fetch_data.py")
    return version


# ---------------------------------------------------------------- get_champion
def test_get_champion_by_chinese_name(temp_db):
    champ = tools.get_champion("锤石")
    assert champ["id"] == "Thresh"
    assert champ["name"] == "锤石"
    assert champ["title"] == "魂锁典狱长"
    assert champ["tags"] == ["Support", "Tank"]
    assert champ["blurb"]
    # 契约：spells 是 [P, Q, W, E, R] 五条
    assert [s["key"] for s in champ["spells"]] == ["P", "Q", "W", "E", "R"]
    assert champ["spells"][1]["name"] == "死亡判决"


def test_get_champion_by_english_id_ignore_case(temp_db):
    for name in ("Thresh", "thresh", "THRESH"):
        assert tools.get_champion(name)["id"] == "Thresh"


def test_get_champion_by_title(temp_db):
    assert tools.get_champion("魂锁典狱长")["id"] == "Thresh"
    assert tools.get_champion("疾风剑豪")["id"] == "Yasuo"


def test_get_champion_fuzzy_match(temp_db):
    # 模糊命中：优先名字包含关键词的，而不是称号里带关键词的
    assert tools.get_champion("锤")["id"] == "Thresh"   # 波比的称号也含"锤"
    assert tools.get_champion("魂锁")["id"] == "Thresh"  # 只在称号里命中
    assert tools.get_champion("圣锤")["id"] == "Poppy"


def test_get_champion_not_found_raises(temp_db):
    with pytest.raises(ValueError, match="未找到英雄"):
        tools.get_champion("不存在的英雄")


def test_get_champion_empty_raises(temp_db):
    with pytest.raises(ValueError):
        tools.get_champion("   ")


# ---------------------------------------------------------------- get_item
def test_get_item_price_and_effect(temp_db):
    item = tools.get_item("无尽之刃")
    assert item["id"] == 3031
    assert item["gold"] == 3500
    assert "攻击力" in item["description"]
    assert item["plaintext"] == "巨幅加强暴击"
    assert item["tags"] == ["CriticalStrike", "Damage"]


def test_get_item_by_numeric_id(temp_db):
    item = tools.get_item("3110")
    assert item["name"] == "冰霜之心"
    assert item["gold"] == 2500


def test_get_item_fuzzy_match(temp_db):
    assert tools.get_item("无尽")["id"] == 3031


def test_get_item_not_found_raises(temp_db):
    with pytest.raises(ValueError, match="未找到装备"):
        tools.get_item("不存在的装备")


# ---------------------------------------------------------------- find_items_by_tag
def test_find_items_by_tag_chinese_maps_to_english(temp_db):
    items = tools.find_items_by_tag("护甲")
    assert [item["id"] for item in items] == [3047, 3110]  # 按 gold 升序
    assert all("Armor" in item["tags"] for item in items)
    # 契约规定的返回字段
    assert set(items[0]) == {"id", "name", "gold", "plaintext", "tags"}


def test_find_items_by_tag_english_case_insensitive(temp_db):
    assert [i["id"] for i in tools.find_items_by_tag("armor")] == [3047, 3110]
    assert [i["id"] for i in tools.find_items_by_tag("ARMOR")] == [3047, 3110]


def test_find_items_by_tag_unknown_raises(temp_db):
    for bad_tag in ("不存在的类别", "zzz"):
        with pytest.raises(ValueError, match="未知装备类别"):
            tools.find_items_by_tag(bad_tag)


def test_find_items_by_tag_empty_raises(temp_db):
    with pytest.raises(ValueError):
        tools.find_items_by_tag("  ")


# ---------------------------------------------------------------- 真实数据库冒烟
def test_real_db_version_is_latest(real_db):
    assert re.fullmatch(r"\d+\.\d+\.\d+", real_db)


def test_real_db_champion_spells_have_q_skill(real_db):
    champ = tools.get_champion("锤石")
    assert champ["id"] == "Thresh"
    spells = {spell["key"]: spell for spell in champ["spells"]}
    assert {"P", "Q", "W", "E", "R"} <= set(spells)
    assert spells["Q"]["name"] == "死亡判决"
    assert spells["Q"]["description"]


def test_real_db_item_has_plain_description(real_db):
    item = tools.get_item("无尽之刃")
    assert item["gold"] > 0
    assert "攻击力" in item["description"]
    assert "<" not in item["description"]  # 已经去掉 HTML 标签了


def test_real_db_armor_items_sorted_and_limited(real_db):
    items = tools.find_items_by_tag("护甲")
    assert 1 <= len(items) <= 30
    golds = [item["gold"] for item in items]
    assert golds == sorted(golds)


# ---------------------------------------------------------------- dispatch 白名单
def test_dispatch_maps_known_tool(temp_db):
    import agent  # agent 依赖 tools/db，但只 import 不会发网络请求

    assert agent.dispatch("get_champion", {"name": "锤石"})["id"] == "Thresh"
    assert agent.dispatch("get_item", {"name": "无尽之刃"})["id"] == 3031
    assert agent.dispatch("find_items_by_tag", {"tag": "护甲"})


def test_dispatch_unknown_tool_raises(temp_db):
    import agent

    with pytest.raises(ValueError, match="未知工具"):
        agent.dispatch("delete_everything", {})


def test_dispatch_bad_args_raises_chinese_error(temp_db):
    import agent

    with pytest.raises(ValueError, match="参数不合法"):
        agent.dispatch("get_champion", {})
