"""M2 检索层测试：离线、不调网络。

覆盖：
- 分词（中文 bigram / 英文小写 / 疑问停用词）
- guides frontmatter 解析、切块规则
- 临时小语料建索引后的命中、top-k 数量/排序、空查询、无关查询
- 索引持久化 save/load 往返
- 真实 knowledge/ + data/lol.db 冒烟（缺文件自动 skip）

运行：uv run python -m pytest tests/test_retrieve.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# src/ 不是 package：和 agent.py 一样手动把 src 加进 sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

import ingest  # noqa: E402
import knowledge  # noqa: E402
import retrieve  # noqa: E402

REAL_DB_PATH = SRC_DIR.parent / "data" / "lol.db"


# ---------------------------------------------------------------- 临时小语料
# 每篇只写和主题强相关的短文本，方便断言"查询命中的是哪一篇"。
DOCS = [
    {
        "doc_id": "guide:armor", "title": "对抗高护甲阵容思路",
        "kind": "guide", "source": "示例攻略（非官方）",
        "text": (
            "对抗高护甲阵容时，物理输出要提前做护甲穿透，比如最后的轻语，"
            "配合暴击装才能打得动对面坦克。\n\n"
            "如果对面回复高，还要考虑重伤装备。"
        ),
    },
    {
        "doc_id": "guide:vision", "title": "视野与节奏",
        "kind": "guide", "source": "示例攻略（非官方）",
        "text": "小龙和大龙刷新前先做视野，河道草丛和三角草是常见的眼位。",
    },
    {
        "doc_id": "champion:Yasuo", "title": "亚索·疾风剑豪",
        "kind": "champion", "source": "Data Dragon 99.9.9（自动生成）",
        "text": (
            "英雄：亚索（疾风剑豪）\n"
            "定位：战士（Fighter）、刺客（Assassin）\n"
            "Q 技能「斩钢闪」：向前突刺，命中两次后可击飞敌人。\n"
            "W 技能「风之障壁」：生成风墙阻挡飞行道具。"
        ),
    },
    {
        "doc_id": "item:3031", "title": "无尽之刃",
        "kind": "item", "source": "Data Dragon 99.9.9（自动生成）",
        "text": "装备：无尽之刃\n价格：3500 金币\n效果：75 攻击力 25% 暴击几率",
    },
]


def make_chunks(max_chars: int = 200) -> list[dict]:
    chunks: list[dict] = []
    for document in DOCS:
        chunks.extend(knowledge.chunk_document(document, max_chars=max_chars))
    return chunks


@pytest.fixture()
def index() -> ingest.Index:
    """用固定小语料建索引；version/fingerprint 显式给值，测试不依赖真实 db。"""
    return ingest.build_index(
        make_chunks(), dim=256, version="99.9.9", fingerprint="test-fingerprint"
    )


# ---------------------------------------------------------------- 分词 & 切块
def test_tokenize_chinese_bigram_and_english_lower():
    tokens = ingest.tokenize("护甲Armor")
    assert "护甲" in tokens
    assert "armor" in tokens


def test_tokenize_drops_question_stopwords():
    tokens = ingest.tokenize("今天天气怎么样")
    assert "怎么" not in tokens
    assert "么样" not in tokens


def test_chunk_document_respects_max_chars_and_anchor():
    long_doc = {
        "doc_id": "guide:long", "title": "长文攻略", "kind": "guide",
        "source": "示例攻略（非官方）",
        "text": "第一段。" * 20 + "\n\n" + "第二段。" * 20,
    }
    chunks = knowledge.chunk_document(long_doc, max_chars=120)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk["doc_id"] == "guide:long"
        # 每块都带《标题》锚点，切块后实体名不丢失
        assert chunk["text"].startswith("《长文攻略》")
        # text 含锚点后仍不超过 max_chars
        assert len(chunk["text"]) <= 120
        assert len(chunk["snippet"]) <= knowledge.SNIPPET_CHARS + 1


def test_load_guides_parses_frontmatter(tmp_path):
    (tmp_path / "10-demo.md").write_text(
        "---\n"
        "title: 测试攻略标题\n"
        "tags: [装备, 护甲]\n"
        "source: 示例攻略（非官方）\n"
        "---\n\n"
        "这是正文，讲护甲穿透。\n",
        encoding="utf-8",
    )
    docs = knowledge.load_guides(tmp_path)
    assert len(docs) == 1
    doc = docs[0]
    assert doc["doc_id"] == "guide:10-demo"
    assert doc["title"] == "测试攻略标题"
    assert doc["kind"] == "guide"
    assert doc["source"] == "示例攻略（非官方）"
    assert "这是正文" in doc["text"]
    assert "装备" in doc["text"]  # frontmatter tags 会拼进正文参与检索


def test_load_guides_missing_dir_returns_empty(tmp_path):
    assert knowledge.load_guides(tmp_path / "not-exists") == []


# ---------------------------------------------------------------- 检索
def test_search_hits_correct_guide(index):
    results = retrieve.search("对抗高护甲怎么出装", index=index)
    assert results
    assert results[0]["doc_id"] == "guide:armor"
    assert results[0]["kind"] == "guide"
    assert results[0]["score"] > 0


def test_search_hits_correct_item(index):
    results = retrieve.search("无尽之刃多少钱", index=index)
    assert results
    assert results[0]["doc_id"] == "item:3031"


def test_search_hits_correct_champion(index):
    results = retrieve.search("亚索 Q 技能", index=index)
    assert results
    assert results[0]["doc_id"] == "champion:Yasuo"


def test_search_top_k_count_and_sorted(index):
    results = retrieve.search("护甲 装备 视野 技能", k=3, index=index)
    assert 0 < len(results) <= 3
    scores = [result["score"] for result in results]
    assert scores == sorted(scores, reverse=True)
    assert all(score >= retrieve.DEFAULT_MIN_SCORE for score in scores)
    # 返回字段严格按契约
    assert set(results[0]) == {"doc_id", "title", "kind", "source", "snippet", "score"}


def test_search_empty_query_raises(index):
    with pytest.raises(ValueError, match="不能为空"):
        retrieve.search("   ", index=index)


def test_search_unrelated_query_returns_empty(index):
    # 全是语料里没出现过的字 → 没有可用特征，直接 []
    assert retrieve.search("今天天气怎么样", index=index) == []
    assert retrieve.search("魑魅魍魉", index=index) == []


def test_search_k_zero_returns_empty(index):
    assert retrieve.search("护甲", k=0, index=index) == []


def test_search_dedupes_same_document(index):
    results = retrieve.search("对抗高护甲阵容", k=4, index=index)
    doc_ids = [result["doc_id"] for result in results]
    assert len(doc_ids) == len(set(doc_ids))


# ---------------------------------------------------------------- 持久化往返
def test_save_and_load_index_roundtrip(index, tmp_path):
    path = tmp_path / "index.json"
    ingest.save_index(index, path)
    assert path.exists()

    loaded = ingest.load_index(path)
    assert loaded is not None
    assert loaded.dim == index.dim
    assert loaded.chunk_count == index.chunk_count
    assert loaded.vocab_size == index.vocab_size
    assert loaded.fingerprint == index.fingerprint
    assert loaded.norms is not None and len(loaded.norms) == index.chunk_count
    assert len(loaded.token_weights) == index.chunk_count

    # 加载后的索引仍然能正确检索（精确稀疏权重也一起持久化了）
    results = retrieve.search("对抗高护甲怎么出装", index=loaded)
    assert results and results[0]["doc_id"] == "guide:armor"


def test_load_index_bad_file_returns_none(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("not-json", encoding="utf-8")
    assert ingest.load_index(path) is None


def test_fingerprint_changes_are_detected():
    # 同一份语料两次构建指纹一致；dim/schema 变化会改变指纹（旧索引失效）
    fp1 = ingest.current_fingerprint(dim=256)
    fp2 = ingest.current_fingerprint(dim=256)
    fp3 = ingest.current_fingerprint(dim=512)
    assert fp1 == fp2
    assert fp1 != fp3
    assert f"v{ingest.INDEX_SCHEMA}" in fp1


# ---------------------------------------------------------------- 真实语料冒烟
def _require_real_index():
    if not REAL_DB_PATH.exists():
        pytest.skip("data/lol.db 不存在，先运行 src/fetch_data.py")
    index = ingest.get_index()
    if index.chunk_count == 0:
        pytest.skip("语料为空，先运行 src/fetch_data.py / 准备 knowledge/guides")
    return index


def test_real_index_champion_query_hits_thresh():
    index = _require_real_index()
    results = retrieve.search("锤石的 Q 技能", index=index)
    assert results
    assert results[0]["doc_id"] == "champion:Thresh"


def test_real_index_guide_query_hits_guide():
    index = _require_real_index()
    results = retrieve.search("对抗高护甲阵容该出什么装备", index=index)
    assert results
    assert results[0]["kind"] == "guide"


def test_real_index_unrelated_query_returns_empty():
    index = _require_real_index()
    assert retrieve.search("今天天气怎么样", index=index) == []


def test_real_pipeline_tools_search_knowledge():
    """M2/M3 端到端（离线）：tools.search_knowledge → retrieve → ingest。"""
    _require_real_index()
    import tools

    result = tools.search_knowledge("对抗高护甲阵容怎么出装")
    assert result["query"] == "对抗高护甲阵容怎么出装"
    assert result["results"], "真实语料上应该能检索到攻略"
    first = result["results"][0]
    assert set(first) == {"title", "kind", "snippet", "score"}
    assert isinstance(first["score"], float)
