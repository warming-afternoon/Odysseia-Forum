"""全文搜索文本分词工具。"""

import rjieba


def build_search_vector_text(title: str | None, excerpt: str | None) -> list[str] | None:
    """用 rjieba 对 title + excerpt 分词，返回 token 列表。

    用于填充 PostgreSQL search_vector 列（通过 array_to_tsvector）。
    所有 token 转为小写以确保大小写不敏感匹配。
    """
    parts = []
    if title:
        parts.append(title)
    if excerpt:
        parts.append(excerpt)
    if not parts:
        return None
    combined = " ".join(parts)
    tokens = list(rjieba.cut(combined))
    filtered = [t.lower().strip() for t in tokens if t.strip()]
    return filtered if filtered else None
