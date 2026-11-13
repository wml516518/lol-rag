"""M2 向量化与索引：不调外部 embedding 服务，手写「字符 n-gram + TF-IDF + 哈希向量」。

一、为什么第一版不用真实 embedding？
  - DeepSeek 没有 embedding 接口，项目里也没有别的 embedding key；
  - 引入本地向量模型（sentence-transformers 等）动辄几百 MB，违背"别引重型依赖"；
  - 更重要的是：先用 100 行代码把「文本 → 向量 → 余弦相似度」跑通，你会真正理解
    向量检索到底在干什么；以后要换成真实 embedding，只需要替换 embed_text，
    索引/检索/Agent 工具这一整套都不用动。

二、这一版怎么把文本变成向量？
  1) 分词（特征）：中文没有空格，用「字符 bigram」切，例如
       "对抗高护甲" → 对抗 / 抗高 / 高护 / 护甲
     英文/数字转小写后按词切，例如 "Armor 3031" → armor / 3031。
     这就是所谓的「字符 n-gram」：不依赖词典，小语料上召回稳、无 OOV 问题。
  2) TF-IDF 加权：TF = 词频 / 本块总词数；IDF = log((N+1)/(df+1)) + 1。
     越普遍的 bigram（比如"装备""英雄"）权重越低，越稀有的越能区分文档。
  3) 哈希到固定维度（feature hashing）：把每个 token 用 md5 映射到 0..511 的桶，
     并取一个 ±1 符号（signed hashing）。好处是不用维护"词表→下标"的巨大字典，
     内存固定、新词也能直接用；代价是不同 token 可能撞桶，但用符号抵消后
     相似度在概率上仍然接近真实的 TF-IDF 内积。
  4) L2 归一化：向量长度变成 1 后，两个向量的点积 = 余弦相似度 ∈ [-1, 1]，
     排序时直接比较点积即可。

三、局限与升级路径
  局限：只认字面重合，不理解语义——"坦克"和"肉"、"ADC"和"射手"未必能互相命中；
        哈希碰撞也会损失一点精度。
  升级：把 embed_text 换成真实 embedding API / 本地模型（维度用模型输出），
        cosine top-k 逻辑完全复用；再下一步可以在检索前加 query 改写/混合检索。

四、索引结构
  Index: dim / version / fingerprint / built_at / chunks / matrix(n×dim) / idf(token→权重)
         + token_weights(每块精确稀疏 TF-IDF) / norms(每块稀疏向量长度)
  - chunks 保存每块元数据（doc_id/title/kind/source/snippet），检索命中后直接返回；
  - matrix 是哈希向量矩阵，用于 numpy 矩阵乘法「粗排」；
  - token_weights/norms 是精确稀疏权重，用于对粗排候选「精排」：
    哈希会撞桶（比如查询里的"能是"恰好和某篇文档里的高频词落到同一维），
    粗排 top-N 里可能混进不相关结果；用精确稀疏余弦重排一次就能修正。
  - 索引可持久化到 data/knowledge_index.json，避免每次进程启动都重新算。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

import knowledge
from config import DATA_DIR

# 哈希向量维度：512 维在"小语料 + 中文 bigram"场景下够用；
# 维度越大碰撞越少、内存越大，是典型的精度/成本权衡。
DIM = 512

INDEX_PATH = DATA_DIR / "knowledge_index.json"

# 索引结构/分词算法版本：改动切块或向量化逻辑时把它 +1，
# 持久化文件的 fingerprint 就会变化，旧索引自动失效、重新构建。
INDEX_SCHEMA = 3

# 中文按「连续汉字串」切，英文/数字按「连续字母数字」切
CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
LATIN_RE = re.compile(r"[a-z0-9]+")

# 疑问词/语气词停用词：它们几乎出现在每篇攻略里（"怎么出装""什么是…"），
# 却不携带主题信息。如果不剔除，"今天天气怎么样"这种无关问题也会因为
# 命中"怎么/么样"而检索出一堆攻略；剔除后无关查询会干净地返回空结果。
QUESTION_STOPWORDS = {
    "什么", "怎么", "么样", "怎样", "如何", "多少", "哪个", "哪些",
    "为什", "是否", "是不", "不是", "有没有",
}

# 去掉 df 占比过高的 token（更通用版的"停用词"）：
# 当一个 bigram 出现在超过一半的 chunk 里，它区分文档的能力已经趋近于 0，
# 保留只会给所有向量加噪声，直接丢掉。小语料（测试）不启用，避免误伤关键词。
MAX_DF_RATIO = 0.5
PRUNE_MIN_CHUNKS = 20

# 进程内缓存：同一个进程里多次检索只建一次索引
_INDEX: Index | None = None


@dataclass
class Index:
    """内存索引：矩阵的每一行 = 一个 chunk 的哈希向量（已 L2 归一化）。

    除了哈希向量，还保留一份「精确稀疏权重」token_weights/norms：
    哈希向量用于 numpy 矩阵乘法的快速粗排，精确稀疏权重用于对候选做精排，
    修正哈希碰撞带来的假阳性（详见 retrieve.search 里的粗排+精排说明）。
    """

    dim: int
    version: str
    fingerprint: str
    built_at: str
    chunks: list[dict]
    matrix: np.ndarray
    idf: dict[str, float] = field(default_factory=dict)
    token_weights: list[dict[str, float]] = field(default_factory=list)
    norms: np.ndarray | None = None

    @property
    def vocab_size(self) -> int:
        return len(self.idf)

    @property
    def chunk_count(self) -> int:
        return len(self.chunks)

    @property
    def doc_count(self) -> int:
        return len({chunk["doc_id"] for chunk in self.chunks})


# ---------------------------------------------------------------- 分词 & 向量


def tokenize(text: str) -> list[str]:
    """把文本切成检索特征：中文 bigram + 英文小写词/数字（去掉疑问停用词）。"""
    text = (text or "").lower()
    tokens: list[str] = []
    for run in CJK_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)  # 单字也保留，避免"甲"这种查询完全没特征
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    tokens.extend(LATIN_RE.findall(text))
    return [token for token in tokens if token not in QUESTION_STOPWORDS]


def _hash_token(token: str, dim: int) -> tuple[int, float]:
    """token → (桶下标, ±1 符号)。符号用于抵消哈希碰撞带来的系统性偏差。"""
    digest = hashlib.md5(token.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:4], "little") % dim
    sign = 1.0 if digest[4] & 1 else -1.0
    return bucket, sign


def _weights(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    """tokens → {token: TF-IDF 权重}（未归一化）。索引里没有的 token 直接丢弃。"""
    if not tokens:
        return {}
    counts = Counter(tokens)
    total = len(tokens)
    return {
        token: (count / total) * idf[token]
        for token, count in counts.items()
        if idf.get(token, 0.0) > 0.0
    }


def _normalize(vector: np.ndarray) -> np.ndarray:
    """L2 归一化：向量长度变 1 后，点积就是余弦相似度。"""
    norm = float(np.linalg.norm(vector))
    if norm > 0:
        vector /= norm
    return vector


def _vector_from_weights(weights: dict[str, float], dim: int) -> np.ndarray:
    """{token: 权重} → 固定维度哈希向量（signed feature hashing）。"""
    vector = np.zeros(dim, dtype=np.float32)
    for token, weight in weights.items():
        bucket, sign = _hash_token(token, dim)
        vector[bucket] += sign * weight
    return _normalize(vector)


def _vectorize(tokens: list[str], idf: dict[str, float], dim: int) -> np.ndarray:
    """tokens → 稀疏 TF-IDF → [dim] 稠密向量 → L2 归一化。"""
    return _vector_from_weights(_weights(tokens, idf), dim)


def _build_idf(token_lists: list[list[str]]) -> dict[str, float]:
    """平滑 IDF：log((N+1)/(df+1)) + 1，保证只出现一次的词权重也 > 0。

    同时做一次「高频 token 剪枝」：df 占比超过 MAX_DF_RATIO 的 token 直接从
    idf 表里去掉（等价于所有权重为 0），避免"技能""装备"这种满语料都有的词
    稀释相似度。语料太小时不剪枝，防止把测试里的关键词误删。
    """
    n_docs = len(token_lists)
    df: Counter[str] = Counter()
    for tokens in token_lists:
        df.update(set(tokens))  # 同一块里重复出现只算一次 df

    prune = n_docs >= PRUNE_MIN_CHUNKS
    max_df = MAX_DF_RATIO * n_docs if prune else float("inf")
    return {
        token: math.log((n_docs + 1) / (freq + 1)) + 1.0
        for token, freq in df.items()
        if freq <= max_df
    }


# ---------------------------------------------------------------- 建索引


def current_fingerprint(dim: int = DIM, guides_dir: Path | None = None) -> str:
    """语料指纹 + 向量化配置：任何一边变了，持久化索引就应该重建。"""
    return f"{knowledge.corpus_fingerprint(guides_dir)}:{dim}:v{INDEX_SCHEMA}"


def build_index(
    chunks: list[dict] | None = None,
    dim: int = DIM,
    guides_dir: Path | None = None,
    version: str | None = None,
    fingerprint: str | None = None,
) -> Index:
    """chunks → Index。chunks 不传时从 knowledge.load_documents() 自动构建。

    version / fingerprint 可显式传入（测试用），默认从 db + guides 现状算。
    """
    if chunks is None:
        chunks = knowledge.build_corpus(guides_dir=guides_dir)

    version = version if version is not None else knowledge.current_version()
    fingerprint = (
        fingerprint if fingerprint is not None
        else current_fingerprint(dim, guides_dir)
    )

    token_lists = [tokenize(chunk.get("text", "")) for chunk in chunks]
    idf = _build_idf(token_lists)

    # ① 精确稀疏权重：每块 {token: tf-idf}，用于检索时的精排（无碰撞误差）
    token_weights: list[dict[str, float]] = []
    norms = np.zeros(len(chunks), dtype=np.float32)
    for row, tokens in enumerate(token_lists):
        weights = _weights(tokens, idf)
        token_weights.append(weights)
        norms[row] = float(np.linalg.norm(list(weights.values())))

    # ② 哈希向量矩阵：固定 512 维，用于一次矩阵乘法快速粗排
    matrix = np.zeros((len(chunks), dim), dtype=np.float32)
    for row, weights in enumerate(token_weights):
        matrix[row] = _vector_from_weights(weights, dim)

    return Index(
        dim=dim,
        version=version,
        fingerprint=fingerprint,
        built_at=datetime.now().isoformat(timespec="seconds"),
        chunks=chunks,
        matrix=matrix,
        idf=idf,
        token_weights=token_weights,
        norms=norms,
    )


# ---------------------------------------------------------------- 查询向量


def embed_query(query: str, index: Index) -> np.ndarray | None:
    """查询文本 → 哈希向量（粗排用）。返回 None 表示没有可用特征。"""
    tokens = tokenize(query)
    if not tokens:
        return None
    vector = _vectorize(tokens, index.idf, index.dim)
    if not vector.any():
        return None
    return vector


def query_weights(query: str, index: Index) -> tuple[dict[str, float], float] | None:
    """查询文本 → ({token: tf-idf}, 向量长度)，精排时算精确余弦用。"""
    tokens = tokenize(query)
    if not tokens:
        return None
    weights = _weights(tokens, index.idf)
    norm = float(np.linalg.norm(list(weights.values())))
    if not weights or norm == 0:
        return None
    return weights, norm


def exact_cosine(
    query_weights_map: dict[str, float],
    query_norm: float,
    chunk_weights: dict[str, float],
    chunk_norm: float,
) -> float:
    """精确稀疏余弦相似度：只遍历查询里的几个 token 查该 chunk 的权重。

    和哈希向量相比没有"撞桶"误差，用于对粗排候选做精排。
    """
    if chunk_norm <= 0:
        return 0.0
    dot = sum(
        weight * chunk_weights.get(token, 0.0)
        for token, weight in query_weights_map.items()
    )
    return dot / (query_norm * chunk_norm)


# ---------------------------------------------------------------- 持久化


def _to_sparse(vector: np.ndarray) -> dict:
    """把稠密向量存成稀疏 JSON：只有非零桶。"""
    indices = np.nonzero(vector)[0]
    return {
        "indices": indices.tolist(),
        "values": [round(float(vector[i]), 6) for i in indices],
    }


def save_index(index: Index, path: Path = INDEX_PATH) -> Path:
    """把索引（哈希向量 + 精确稀疏权重 + idf + chunk 元数据）写成 JSON。"""
    payload = {
        "schema": INDEX_SCHEMA,
        "dim": index.dim,
        "version": index.version,
        "fingerprint": index.fingerprint,
        "built_at": index.built_at,
        "vocab_size": index.vocab_size,
        "chunk_count": index.chunk_count,
        "doc_count": index.doc_count,
        "idf": index.idf,
        "chunks": [
            {key: chunk[key] for key in ("doc_id", "title", "kind", "source", "snippet")}
            for chunk in index.chunks
        ],
        "vectors": [_to_sparse(row) for row in index.matrix],
        "token_weights": index.token_weights,
        "norms": [] if index.norms is None else [round(float(x), 6) for x in index.norms],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def load_index(path: Path = INDEX_PATH) -> Index | None:
    """读回 save_index 写出的索引；文件缺失/损坏/格式不符时返回 None。"""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema") != INDEX_SCHEMA:
            return None
        dim = int(payload["dim"])
        chunks = list(payload["chunks"])
        vectors = list(payload["vectors"])
        idf = {str(key): float(value) for key, value in payload["idf"].items()}
        if len(chunks) != len(vectors):
            return None

        matrix = np.zeros((len(chunks), dim), dtype=np.float32)
        for row, sparse in enumerate(vectors):
            for column, value in zip(sparse["indices"], sparse["values"]):
                matrix[row, int(column)] = float(value)

        token_weights = [
            {str(token): float(weight) for token, weight in weights.items()}
            for weights in payload.get("token_weights", [])
        ]
        norms = np.asarray(payload.get("norms", []), dtype=np.float32)
        if len(token_weights) != len(chunks) or len(norms) != len(chunks):
            token_weights, norms = [], np.zeros(len(chunks), dtype=np.float32)

        return Index(
            dim=dim,
            version=str(payload.get("version", "unknown")),
            fingerprint=str(payload.get("fingerprint", "")),
            built_at=str(payload.get("built_at", "")),
            chunks=chunks,
            matrix=matrix,
            idf=idf,
            token_weights=token_weights,
            norms=norms,
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------- 进程级入口


def get_index(force_refresh: bool = False, use_persisted: bool = True) -> Index:
    """拿进程内缓存的索引；缓存没有时先尝试读持久化文件，再退回现场构建。

    持久化文件只有在 fingerprint 与当前语料一致时才复用，
    避免 data/lol.db 刷新后还在用旧索引。
    """
    global _INDEX
    if _INDEX is not None and not force_refresh:
        return _INDEX

    if use_persisted:
        loaded = load_index()
        if loaded is not None and loaded.fingerprint == current_fingerprint(loaded.dim):
            _INDEX = loaded
            return _INDEX

    _INDEX = build_index()
    return _INDEX


def main() -> None:
    """构建索引 → 落盘 → 打印统计，顺便跑一条示例查询验证。"""
    started = time.perf_counter()

    print("[1/3] 读取语料（knowledge/guides + data/lol.db 自动档案）...")
    chunks = knowledge.build_corpus()
    if not chunks:
        print("⚠️ 语料为空：请先运行 `uv run python src/fetch_data.py` 生成 data/lol.db，"
              "或在 knowledge/guides/ 放攻略文档。")
        return

    by_kind: dict[str, set[str]] = {}
    for chunk in chunks:
        by_kind.setdefault(chunk["kind"], set()).add(chunk["doc_id"])
    print(
        f"      文档 {sum(len(ids) for ids in by_kind.values())} 篇"
        f"（攻略 {len(by_kind.get('guide', ()))} / 英雄 {len(by_kind.get('champion', ()))} / "
        f"装备 {len(by_kind.get('item', ()))}），切块 {len(chunks)} 个"
    )

    print(f"[2/3] 构建向量索引（{DIM} 维，字符 bigram + TF-IDF + 哈希）...")
    index = build_index(chunks)
    elapsed = time.perf_counter() - started
    print(
        f"      词表规模 {index.vocab_size} 个 token，"
        f"矩阵形状 {index.matrix.shape}，耗时 {elapsed:.2f}s"
    )

    print("[3/3] 持久化 + 示例查询...")
    path = save_index(index)
    size_kb = path.stat().st_size / 1024
    print(
        f"      已写入 {path}（{size_kb:.0f}KB，版本 {index.version}，"
        f"fingerprint {index.fingerprint[:12]}…）"
    )

    demo_query = "对抗高护甲阵容怎么出装"
    demo_weights = query_weights(demo_query, index)
    if demo_weights is not None and index.norms is not None:
        weights, query_norm = demo_weights
        scores = np.array([
            exact_cosine(weights, query_norm, chunk_weights, float(index.norms[row]))
            for row, chunk_weights in enumerate(index.token_weights)
        ])
        top = np.argsort(-scores)[:3]
        print(f'\n示例查询：「{demo_query}」')
        for rank, row in enumerate(top, 1):
            chunk = chunks[row]
            print(
                f"  {rank}. score={float(scores[row]):.3f} "
                f"[{chunk['kind']}] {chunk['title']} — {chunk['snippet'][:40]}…"
            )


if __name__ == "__main__":
    main()
