"""M0：从 Data Dragon 拉取英雄/装备/英雄技能 → 归一化(填空 ①② 已完成) → 存 JSON + SQLite。

运行:  uv run python src/fetch_data.py

M0 升级（当前版本 16.22.1）：
  - 版本不再写死，取 api/versions.json[0]，永远跟随最新；
  - 额外拉一次 championFull.json（含被动 passive 与 Q/W/E/R spells），
    给每个英雄生成 spells 列表，用来回答"锤石的 Q 技能是什么"这类问题；
  - item.json 的 description 是富文本（<mainText><stats>...），这里去标签、
    反转义 HTML 实体，存成模型能直接读的纯文本效果说明。

填空 ① 和 ② 的字段映射保持原样，只是按契约补上 spells / description。
"""
from __future__ import annotations

import html
import json
import re

import httpx

import db
from config import DATA_DIR, DD_BASE

# Data Dragon 的富文本长这样:
#   "<mainText><stats><attention>75</attention>攻击力<br></stats></mainText>"
# 目标是 "75 攻击力" 这种干净的一行文本。
HTML_TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")

# championFull.json 里 spells 的顺序就是 Q/W/E/R，被动单独放在 passive 字段
SPELL_KEYS = ("Q", "W", "E", "R")


def fetch_json(client: httpx.Client, url: str):
    """带状态码检查的 GET。已写好，直接用。"""
    r = client.get(url)
    r.raise_for_status()
    return r.json()


def latest_version(client: httpx.Client) -> str:
    versions = fetch_json(client, f"{DD_BASE}/api/versions.json")
    return versions[0]


def strip_html(text: str) -> str:
    """富文本 → 纯文本：去 HTML 标签、反转义实体、把空白压成单个空格。"""
    if not text:
        return ""
    # 先把标签换成空格，避免 "<br>" 把前后两段文字粘成一个词
    plain = HTML_TAG_RE.sub(" ", text)
    # 再反转义 &nbsp; &amp; 之类实体（例如 "&nbsp;" 会变成不断行空格 \xa0）
    plain = html.unescape(plain)
    plain = plain.replace("\xa0", " ")
    return WHITESPACE_RE.sub(" ", plain).strip()


# ---------------------------------------------------------------- 填空 ①
# 把原始 champion.json "拍平"成列表，每个元素形如:
#   {"id": "Ahri", "name": "阿狸", "title": "九尾妖狐",
#    "tags": ["Mage", "Assassin"], "blurb": "...", "spells": [...]}
# 提示: raw["data"] 是 {英雄英文名: 详情字典}，用 .items() 遍历。
#
# 注意 zh_CN 的 champion.json 里 name/title 是反的：
#   name  = "暗裔剑魔"（称号）   title = "亚托克斯"（名字）
# 所以这里把两者对调，保证 "name" 永远是英雄名、"title" 永远是称号，
# 这样 get_champion("亚托克斯") 和 get_champion("暗裔剑魔") 都能命中。
def normalize_spells(full_champ: dict) -> list[dict]:
    """从 championFull.json 的单个英雄里提取 [P, Q, W, E, R] 技能列表。"""
    spells = []
    passive = full_champ.get("passive") or {}
    if passive.get("name") or passive.get("description"):
        spells.append({
            "key": "P",
            "name": passive.get("name", ""),
            "description": strip_html(passive.get("description", "")),
        })
    for i, spell in enumerate(full_champ.get("spells") or []):
        if i >= len(SPELL_KEYS):
            break  # 正常只有 4 个，防御性写法
        spells.append({
            "key": SPELL_KEYS[i],
            "name": spell.get("name", ""),
            "description": strip_html(spell.get("description", "")),
        })
    return spells


def normalize_champions(raw: dict, full_raw: dict | None = None) -> list[dict]:
    # full_raw 是可选的：只跑 champion.json 也能工作（spells 为空），
    # 合并时用英文 id 当键（两份数据的键都是 Aatrox/Thresh 这种 id）。
    full_by_id = (full_raw or {}).get("data", {})
    champions = []
    for champ_id, details in raw["data"].items():
        champion = {
            "id": champ_id,
            "name": details["title"],   # 见上方注释：zh_CN 里 title 才是英雄名
            "title": details["name"],   # name 才是称号
            "tags": details["tags"],
            "blurb": details["blurb"],
        }
        full_champ = full_by_id.get(champ_id)
        champion["spells"] = normalize_spells(full_champ) if full_champ else []
        champions.append(champion)
    return champions


# ---------------------------------------------------------------- 填空 ②
# 把原始 item.json "拍平"成列表，每个元素形如:
#   {"id": 1001, "name": "草鞋", "gold": 300,
#    "plaintext": "...", "description": "纯文本效果", "tags": ["Boots"]}
# 提示: raw["data"] 的键是字符串数字；gold 在 raw["data"][k]["gold"]["total"]。
#
# 这里刻意做两步过滤，只留「召唤师峡谷可购买」的装备（契约验收 254 件）：
#   - maps["11"] 是召唤师峡谷；过滤掉「任务：上路」「先锋之眼」这类任务道具、
#     娱乐模式专属道具和已下架装备；
#   - gold.purchasable 过滤掉不能购买的占位装备。
# 全量 863 件里混着这些垃圾条目，不过滤的话 find_items_by_tag("护甲")
# 会返回「任务：辅助」这种结果，直接毁掉问答 Demo 的质量——数据干净比数据多更重要。
def normalize_items(raw: dict) -> list[dict]:
    items = []
    for k, v in raw["data"].items():
        if not v["maps"].get("11", False):
            continue    # 只保留召唤师峡谷的装备
        if not v["gold"].get("purchasable", True):
            continue    # 只保留可购买的装备
        items.append({
            "id": int(k),
            "name": v["name"],
            "gold": v["gold"]["total"],
            "plaintext": v.get("plaintext", ""),
            "description": strip_html(v.get("description", "")),
            "tags": v.get("tags", []),
        })
    return items


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    with httpx.Client(timeout=30) as client:
        version = latest_version(client)
        print(f"[1/5] 最新版本: {version}")

        champs_raw = fetch_json(
            client, f"{DD_BASE}/cdn/{version}/data/zh_CN/champion.json"
        )
        items_raw = fetch_json(
            client, f"{DD_BASE}/cdn/{version}/data/zh_CN/item.json"
        )
        # 第 3 个请求：championFull.json，额外拿到被动和技能描述
        full_raw = fetch_json(
            client, f"{DD_BASE}/cdn/{version}/data/zh_CN/championFull.json"
        )
        print(
            f"[2/5] 原始数据已下载: 英雄 {len(champs_raw['data'])} 个, "
            f"装备 {len(items_raw['data'])} 件, "
            f"技能文件 {len(full_raw['data'])} 个英雄"
        )

        # 原始数据留档，方便你打开观察结构（data/* 已被 .gitignore 忽略）
        (DATA_DIR / f"champion_{version}.json").write_text(
            json.dumps(champs_raw, ensure_ascii=False), encoding="utf-8"
        )
        (DATA_DIR / f"item_{version}.json").write_text(
            json.dumps(items_raw, ensure_ascii=False), encoding="utf-8"
        )
        (DATA_DIR / f"champion_full_{version}.json").write_text(
            json.dumps(full_raw, ensure_ascii=False), encoding="utf-8"
        )

        print("[3/5] 归一化中：英雄(含被动/技能) + 装备(含纯文本效果)...")
        champions = normalize_champions(champs_raw, full_raw)
        items = normalize_items(items_raw)

        print(f"[4/5] 入库: {db.DB_PATH}")
        db.init_db()  # 旧库会自动 ALTER TABLE 补 spells/description 列
        db.save_champions(version, champions)
        db.save_items(version, items)

        # 回读校验：确认 meta.version、行数、技能/效果字段都真的写进去了
        db_version = db.get_meta_version()
        db_champions = db.query_champions()
        db_items = db.query_items()
        thresh = next((c for c in db_champions if c["id"] == "Thresh"), None)
        item_3031 = next((i for i in db_items if i["id"] == 3031), None)
        print(
            f"[5/5] 回读校验: version={db_version}, "
            f"英雄={len(db_champions)}, 装备={len(db_items)}, "
            f"spells(锤石)={len(thresh['spells']) if thresh else 0}, "
            f"description(无尽之刃)={bool(item_3031 and item_3031['description'])}"
        )
        size_kb = db.DB_PATH.stat().st_size / 1024
        print(
            f"✅ M0 完成! 最新版本 {version} / 英雄 {len(champions)} / "
            f"装备 {len(items)} / 数据库 {size_kb:.0f}KB"
        )


if __name__ == "__main__":
    main()
