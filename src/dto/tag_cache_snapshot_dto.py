from dataclasses import dataclass


@dataclass(frozen=True)
class TagCacheSnapshotDTO:
    """脱离 ORM 会话、完成构建后一次替换的标签缓存资料。"""

    id_to_name: dict[int, str]
    """标准标签 ID 到名称的映射"""

    name_to_ids: dict[str, list[int]]
    """名称到标准标签 ID 集合的映射"""

    unique_tag_names: list[str]
    """排序后的全部有效标签名称"""

    global_merged_tags: list[str]
    """已索引帖子使用的有效标签名称"""
