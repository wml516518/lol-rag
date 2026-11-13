"""M2 检索层：输入一句自然语言问题，返回最相关的知识块（cosine top-k）。

整个检索链路：
  1. 查询 → 哈希向量：和建索引时完全相同的分词/TF-IDF/哈希规则（ingest.embed_query）；
  2. 粗排：索引矩阵的每一行都是 L2 归一化过的 chunk 哈希向量，
     所以「矩阵 @ 查询向量」= 每块与查询的余弦相似度，一次矩阵乘法算完，快；
  3. 精排：哈希会撞桶（不同 token 落到同一维），可能把不相关文档抬上来。
     于是对粗排 top-N 候选，用保存下来的「精确稀疏 TF-IDF 权重」再算一次
     余弦相似度——没有碰撞误差，排序更准；
  4. 过滤 min_score、按 doc_id 去重、取 top-k。

为什么要有 min_score？用户问"今天天气怎么样"时，字符 bigram 也可能和某个
chunk 撞上几个字。设个阈值直接返回空列表，Agent 才能诚实地回答
"我的数据里没有"，而不是硬凑一段不相关的内容。

用法（CLI）:
    uv run python src/retrieve.py "对抗高护甲阵容怎么出装"

注意：src 是扁平模块集合，所以用 `python src/retrieve.py`（不是 -m src.retrieve）。
"""
from __future__ import annotations

import sys

import numpy as np

import ingest

# 低于这个分数视为噪声，不返回（字符 n-gram 对无关问题的分数通常接近 0）
DEFAULT_MIN_SCORE = 0.05


def search(
    query: str,
    k: int = 4,
    min_score: float = DEFAULT_MIN_SCORE,
    index: ingest.Index | None = None,
) -> list[dict]:
    """检索最相关的 k 个知识块，按余弦相似度降序。

    index 参数是给测试注入临时索引用的；正常调用不传，走 ingest.get_index() 缓存。
    返回 [] 表示没有命中（查询太泛/与语料无关/索引为空），不是错误。
    """
    clean = (query or "").strip()
    if not clean:
        raise ValueError("查询内容不能为空，请输入你想检索的问题")
    if k <= 0:
        return []

    current_index = index if index is not None else ingest.get_index()
    if not current_index.chunks:
        return []

    query_vector = ingest.embed_query(clean, current_index)
    if query_vector is None:
        # 查询里所有 token 都没在语料出现过：肯定没有相关结果
        return []

    # 第一步：哈希向量粗排（numpy 矩阵乘法，一次算完所有 chunk 的相似度）
    coarse_scores = current_index.matrix @ query_vector
    candidate_count = min(len(coarse_scores), max(100, k * 20))
    candidate_rows = np.argsort(-coarse_scores)[:candidate_count]

    # 第二步：精确 TF-IDF 精排，修正哈希碰撞噪声
    exact = ingest.query_weights(clean, current_index)
    token_weights = current_index.token_weights
    norms = current_index.norms
    can_rescore = (
        exact is not None
        and bool(token_weights)
        and norms is not None
        and len(token_weights) == len(current_index.chunks)
        and len(norms) == len(current_index.chunks)
    )

    scored: list[tuple[float, int]] = []
    if can_rescore:
        query_weights, query_norm = exact
        for row in candidate_rows:
            score = ingest.exact_cosine(
                query_weights,
                query_norm,
                token_weights[int(row)],
                float(norms[int(row)]),
            )
            scored.append((score, int(row)))
    else:
        # 旧索引没有精确权重时退回粗排分数（仍可正常使用）
        scored = [(float(coarse_scores[int(row)]), int(row)) for row in candidate_rows]
    scored.sort(key=lambda item: -item[0])

    results: list[dict] = []
    seen_doc_ids: set[str] = set()
    # 长文档会被切成多块，同一个 doc_id 只保留得分最高的那一块，
    # 否则 top-k 可能被同一篇攻略的多个片段占满，浪费给模型的上下文。
    for score, row in scored:
        if score < min_score:
            break  # 已按分数降序，后面只会更低

        chunk = current_index.chunks[row]
        doc_id = chunk["doc_id"]
        if doc_id in seen_doc_ids:
            continue
        seen_doc_ids.add(doc_id)

        results.append({
            "doc_id": doc_id,
            "title": chunk["title"],
            "kind": chunk["kind"],
            "source": chunk["source"],
            "snippet": chunk["snippet"],
            "score": round(float(score), 4),
        })
        if len(results) >= k:
            break

    return results


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：打印 top-k 结果，方便不启动 Agent 也能调试检索质量。"""
    args = list(sys.argv[1:] if argv is None else argv)
    query = " ".join(args).strip()
    if not query:
        print('用法: uv run python src/retrieve.py "你的问题"')
        return 1

    results = search(query)
    if not results:
        print(f"没有检索到相关内容：{query}")
        print("（可能是问题与语料无关，或 knowledge/guides 与 data/lol.db 还没准备好）")
        return 0

    print(f"查询：{query}")
    print(f"命中 {len(results)} 条（按余弦相似度降序）：")
    for rank, result in enumerate(results, 1):
        print(
            f"\n{rank}. [{result['kind']}] {result['title']}  "
            f"score={result['score']}  来源：{result['source']}"
        )
        print(f"   {result['snippet']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
