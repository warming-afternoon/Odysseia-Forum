from typing import Any

from pydantic import BaseModel


class FTSResultDTO(BaseModel):
    """
    关键词 FTS 全文搜索结果的子查询数据传输对象。

    不再持有 Python set[int]，而是持有 SQLAlchemy select() 子查询对象。
    调用方将这些子查询嵌入 Thread.id.in_(stmt) / Thread.id.not_in(stmt)，
    由 PostgreSQL 内部完成过滤，避免 Python ↔ PG 的 ID 列表搬运。
    """

    include_stmts: list = []  # 每个 AND 组一个 select(Thread.id) 子查询
    exclude_stmt: Any = None  # 排除关键词的 select(Thread.id)，无排除词则为 None
    has_include: bool = False
    has_exclude: bool = False

    class Config:
        arbitrary_types_allowed = True
