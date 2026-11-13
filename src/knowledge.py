"""M2 知识库语料层：把「人工攻略 + 官方数据档案」统一成可检索的文档和切块。

为什么需要这一层？
  M0 的 SQLite 是结构化数据，适合回答「多少钱 / 什么属性 / 什么技能」；
  但用户还会问「对抗高护甲阵容怎么出装」「亚索怎么打」「辅助怎么站位」——
  这类答案藏在成段的攻略文本里，SQL 查不出来，需要文本检索（M2）。
  于是先把所有可检索内容统一成「文档 → 切块」，后面的 ingest/retrieve 只认这个结构。

两类语料：
  A. knowledge/guides/*.md —— 另一位同学手写的示例攻略，文件头有 YAML frontmatter：
       ---
       title: 对抗高护甲阵容的装备思路
       tags: [装备, 护甲, 阵容, 出装]
       source: 示例攻略（非官方）
       ---
  B. data/lol.db —— 自动生成「英雄档案」（173 篇）和「装备档案」（254 篇）。
     好处：内容永远和官方数据一致、可复现；fetch_data.py 刷新版本后重新构建索引即可。

统一文档结构（Document）:
    {"doc_id": "guide:02-anti-armor-items" / "champion:Thresh" / "item:3031",
     "title": ..., "kind": "guide" | "champion" | "item",
     "source": ..., "text": ...}

统一记忆块结构（Chunk）:
    {"doc_id", "title", "kind", "source", "text", "snippet"}
    text     —— 给 n-gram/TF-IDF 算向量用的完整块文本
    snippet  —— 截断后给模型看的摘要（检索结果里展示这一项）

切块为什么要做？TF-IDF 向量是把一段文本压成一个点。文档太长时，一个向量会
混进太多主题（比如英雄档案里既有定位又有 5 个技能），检索精度会下降；切成
300-500 字的块，每块主题更集中，命中后喂给模型的上下文也更省 token。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import db
from config import PROJECT_ROOT

# 攻略目录（只读；由另一位同学维护内容，本文件只负责读取）
GUIDES_DIR = PROJECT_ROOT / "knowledge" / "guides"

# 切块上限：契约定的是 ~300-500 字，400 是中间值（中文场景下约 1-2 个自然段）
MAX_CHUNK_CHARS = 400
# snippet 给模型看的长度：攻略块约 400 字，截到 280 字既能保留大部分要点，
# 又不会让一次工具调用返回的 4 条结果把上下文塞满。
SNIPPET_CHARS = 280

# tags 是 Data Dragon 的英文枚举，档案文本里补上中文叫法，
# 这样用户用中文问「有哪些护甲装备」「刺客定位」时也能靠字面命中。
ROLE_CN = {
    "Fighter": "战士", "Tank": "坦克", "Mage": "法师",
    "Assassin": "刺客", "Marksman": "射手", "Support": "辅助",
}
ITEM_TAG_CN = {
    "Damage": "输出", "SpellDamage": "法强", "AttackSpeed": "攻速",
    "CriticalStrike": "暴击", "LifeSteal": "吸血", "Armor": "护甲",
    "SpellBlock": "魔抗", "MagicResist": "魔抗", "Health": "生命",
    "HealthRegen": "回血", "Mana": "法力", "ManaRegen": "回蓝",
    "Boots": "鞋子", "CooldownReduction": "技能急速", "AbilityHaste": "技能急速",
    "ArmorPenetration": "护甲穿透", "MagicPenetration": "法术穿透",
}

# frontmatter 是 Markdown 文件最开头用 --- 包起来的一段 YAML。
# 这里不引入 PyYAML 这种额外依赖，手写一个只认 "key: value" 的极简解析器：
# 语料规范是固定的 title/tags/source，够用且没有供应链/依赖负担。
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """拆出 (meta, body)。没有 frontmatter 时 meta 为空、body 是全文。"""
    match = FRONTMATTER_RE.match(raw)
    if not match:
        return {}, raw

    meta: dict[str, object] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            # tags: [装备, 护甲, 出装] → ["装备", "护甲", "出装"]
            value = [item.strip().strip("\"'") for item in value[1:-1].split(",") if item.strip()]
        else:
            value = value.strip("\"'")
        meta[key] = value
    return meta, raw[match.end():]


def _as_tag_text(tags: object) -> str:
    """tags 可能是 list 也可能是逗号分隔字符串，统一成展示用文本。"""
    if isinstance(tags, list):
        return "、".join(str(tag) for tag in tags if str(tag).strip())
    return str(tags or "").replace(",", "、")


def load_guides(guides_dir: Path | None = None) -> list[dict]:
    """读取 knowledge/guides/*.md → [Document]。目录不存在时返回 []。

    注意：这里只读，不改 knowledge/ 下任何内容（那是另一位同学负责的）。
    """
    root = Path(guides_dir) if guides_dir is not None else GUIDES_DIR
    if not root.exists():
        return []

    documents = []
    for path in sorted(root.glob("*.md")):
        raw = path.read_text(encoding="utf-8", errors="ignore")
        meta, body = _parse_frontmatter(raw)
        body = body.strip()
        if not body:
            continue
        title = str(meta.get("title") or path.stem)
        tags_text = _as_tag_text(meta.get("tags"))
        source = str(meta.get("source") or "示例攻略（非官方）")
        documents.append({
            "doc_id": f"guide:{path.stem}",
            "title": title,
            "kind": "guide",
            "source": source,
            # 把标题和 tags 也拼进正文：用户搜索「护甲 出装」时，
            # frontmatter 里的关键词同样应该参与匹配。
            "text": f"主题：{title}\n标签：{tags_text}\n\n{body}",
        })
    return documents


def current_version() -> str:
    """当前数据版本（来自 db 的 meta 表）；读不到就返回 unknown。"""
    try:
        return db.get_meta_version() or "unknown"
    except Exception:
        return "unknown"


def build_champion_docs() -> list[dict]:
    """由 data/lol.db 生成英雄档案：称号/定位/简介/被动 + Q/W/E/R 技能。"""
    try:
        champions = db.query_champions()
    except Exception:
        # 数据库还没生成时，检索层不应该整个崩掉；guides 仍然可用。
        return []

    version = current_version()
    documents = []
    for champ in champions:
        roles = "、".join(
            f"{ROLE_CN.get(tag, tag)}（{tag}）" for tag in champ.get("tags", [])
        ) or "未知"
        lines = [
            f"英雄：{champ['name']}（{champ['title']}）",
            f"定位：{roles}",
            f"简介：{champ.get('blurb', '')}",
        ]
        for spell in champ.get("spells", []):
            lines.append(
                f"{spell.get('key', '')} 技能「{spell.get('name', '')}」："
                f"{spell.get('description', '')}"
            )
        documents.append({
            "doc_id": f"champion:{champ['id']}",
            "title": f"{champ['name']}·{champ['title']}",
            "kind": "champion",
            "source": f"Data Dragon {version}（自动生成）",
            "text": "\n".join(lines),
        })
    return documents


def build_item_docs() -> list[dict]:
    """由 data/lol.db 生成装备档案：价格 / 纯文本效果 / tags。"""
    try:
        items = db.query_items()
    except Exception:
        return []

    version = current_version()
    documents = []
    for item in items:
        tags_cn = "、".join(
            ITEM_TAG_CN.get(tag, tag) for tag in item.get("tags", [])
        ) or "无"
        tags_en = "、".join(item.get("tags", [])) or "无"
        description = item.get("description") or item.get("plaintext") or "无描述"
        documents.append({
            "doc_id": f"item:{item['id']}",
            "title": item["name"],
            "kind": "item",
            "source": f"Data Dragon {version}（自动生成）",
            "text": (
                f"装备：{item['name']}\n"
                f"价格：{item.get('gold', 0)} 金币\n"
                f"效果：{description}\n"
                f"标签：{tags_cn}（{tags_en}）"
            ),
        })
    return documents


def load_documents(guides_dir: Path | None = None) -> list[dict]:
    """全部文档 = 手写攻略 + 英雄档案 + 装备档案。"""
    return load_guides(guides_dir) + build_champion_docs() + build_item_docs()


def _split_long_unit(unit: str, max_chars: int) -> list[str]:
    """把超长的一行按句末标点拆开；单句仍然超长就硬切。"""
    pieces = re.split(r"(?<=[。！？!?；;])", unit)
    result: list[str] = []
    for piece in pieces:
        piece = piece.strip()
        while len(piece) > max_chars:  # 完全没有标点的超长文本，只能硬切
            result.append(piece[:max_chars])
            piece = piece[max_chars:]
        if piece:
            result.append(piece)
    return result


def _paragraph_units(text: str, max_chars: int) -> list[list[str]]:
    """按空行分段落，每段再拆成不超过 max_chars 的单元（行/句子）。

    返回 [[段1的单元...], [段2的单元...]]：外层列表的每一段内部可以自由拼，
    段与段之间不混进同一个 chunk，保证块的主题不被拼散。
    """
    paragraphs = re.split(r"\n\s*\n", text)
    result: list[list[str]] = []
    for paragraph in paragraphs:
        units: list[str] = []
        for line in paragraph.splitlines():
            line = line.strip()
            if not line:
                continue
            if len(line) <= max_chars:
                units.append(line)
            else:
                units.extend(_split_long_unit(line, max_chars))
        if units:
            result.append(units)
    return result


def _make_chunk(document: dict, text: str, anchor: str) -> dict:
    """组装一个 chunk：text 用于算向量，snippet 用于展示给模型。

    anchor 是「《标题》」前缀，会被拼到每个 chunk 前面。为什么需要？
    长文档切块后，后面的块可能只剩下技能描述、看不到英雄名；而用户的查询
    往往只提到名字（"锤石的 Q"）。给每块都带上标题锚点，实体就不会和
    它所属的文档失联，检索命中率会明显提升。
    """
    anchored_text = f"{anchor}\n{text}"
    snippet = re.sub(r"\s+", " ", anchored_text).strip()
    if len(snippet) > SNIPPET_CHARS:
        snippet = snippet[:SNIPPET_CHARS].rstrip() + "…"
    return {
        "doc_id": document["doc_id"],
        "title": document["title"],
        "kind": document["kind"],
        "source": document["source"],
        "text": anchored_text,
        "snippet": snippet,
    }


def chunk_document(document: dict, max_chars: int = MAX_CHUNK_CHARS) -> list[dict]:
    """把一篇 Document 切成若干 <= max_chars 的 Chunk。

    策略：段内贪心拼行/句，段与段之间强制切开（尊重段落/技能边界）；
    每块都带「《标题》」锚点，避免切块后实体名丢失。
    """
    anchor = f"《{document['title']}》"
    # 预留锚点的长度，保证拼出来的 chunk 仍然不超过 max_chars
    effective_max = max(max_chars - len(anchor) - 1, 50)
    paragraphs = _paragraph_units(document["text"], effective_max)
    chunks: list[dict] = []

    for units in paragraphs:
        buffer: list[str] = []
        buffer_len = 0
        for unit in units:
            if buffer and buffer_len + len(unit) + 1 > effective_max:
                chunks.append(_make_chunk(document, "\n".join(buffer), anchor))
                buffer, buffer_len = [], 0
            buffer.append(unit)
            buffer_len += len(unit) + 1
        if buffer:
            chunks.append(_make_chunk(document, "\n".join(buffer), anchor))

    return chunks


def build_corpus(
    guides_dir: Path | None = None,
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[dict]:
    """完整语料：所有文档 → 所有 chunk。ingest 直接拿它建索引。"""
    chunks: list[dict] = []
    for document in load_documents(guides_dir):
        chunks.extend(chunk_document(document, max_chars=max_chars))
    return chunks


def corpus_fingerprint(guides_dir: Path | None = None) -> str:
    """给语料算一个"指纹"，用来判断持久化索引是否过期。

    什么时候会变：data/lol.db 被 fetch_data.py 刷新（版本/大小/时间变）、
    knowledge/guides 里新增/修改了文档。
    """
    digest = hashlib.sha1()
    digest.update(str(current_version()).encode("utf-8"))

    # 数据库文件本身也纳入指纹（同版本重刷、内容变化也能感知）
    db_path = getattr(db, "DB_PATH", None)
    try:
        if db_path and Path(db_path).exists():
            stat = Path(db_path).stat()
            digest.update(f"db:{stat.st_size}:{stat.st_mtime_ns}".encode())
    except OSError:
        pass

    root = Path(guides_dir) if guides_dir is not None else GUIDES_DIR
    if root.exists():
        for path in sorted(root.glob("*.md")):
            stat = path.stat()
            digest.update(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()


def corpus_stats() -> dict:
    """可读统计：文档数（按 kind 拆开）/ 切块数。ingest 打印用。"""
    documents = load_documents()
    chunks = []
    for document in documents:
        chunks.extend(chunk_document(document))
    by_kind: dict[str, int] = {}
    for document in documents:
        by_kind[document["kind"]] = by_kind.get(document["kind"], 0) + 1
    return {"documents": len(documents), "chunks": len(chunks), "by_kind": by_kind}
