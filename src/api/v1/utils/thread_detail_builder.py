from typing import Any, Dict, List, Optional, Set

from api.v1.schemas.search import AuthorDetail, ThreadDetail
from shared.channel_mapping_utils import ChannelMappingUtils


class ThreadDetailBuilder:
    """
    将 ORM Thread 模型转换为 ThreadDetail 响应模型的工具类。

    处理作者信息嵌套、标签提取、虚拟映射标签计算以及收藏状态标记。
    """

    def __init__(self, channel_mappings_config: Optional[Dict[int, List[Dict]]] = None):
        self.channel_mappings = channel_mappings_config or {}
        self.mapping_utils = ChannelMappingUtils(self.channel_mappings)
        self._global_virtual_tags_map = (
            self.mapping_utils.get_all_channel_virtual_tags_map()
        )

    def build(
        self,
        thread: Any,
        collected_thread_ids: Set[int],
        channel_to_virtual: Optional[Dict[int, List[str]]] = None,
    ) -> ThreadDetail:
        """构建单个 ThreadDetail。

        :param thread: 数据库 ORM Thread 对象
        :param collected_thread_ids: 当前用户已收藏的帖子 ID 集合
        :param channel_to_virtual: 可选的自定义频道到虚拟标签映射。
            如果为 None，使用全局映射。
        """
        if channel_to_virtual is None:
            channel_to_virtual = self._global_virtual_tags_map

        matched_virtual = channel_to_virtual.get(thread.channel_id, [])

        return ThreadDetail(
            thread_id=thread.thread_id,
            guild_id=thread.guild_id,
            channel_id=thread.channel_id,
            title=thread.title,
            author=AuthorDetail.model_validate(thread.author)
            if getattr(thread, "author", None)
            else None,
            created_at=thread.created_at,
            last_active_at=thread.last_active_at,
            reaction_count=thread.reaction_count,
            reply_count=thread.reply_count,
            collection_count=thread.collection_count,
            display_count=thread.display_count,
            first_message_excerpt=thread.first_message_excerpt,
            thumbnail_urls=thread.thumbnail_urls or [],
            tags=[tag.name for tag in thread.tags]
            if getattr(thread, "tags", None)
            else [],
            virtual_tags=list(set(matched_virtual)),
            collected_flag=thread.thread_id in collected_thread_ids,
        )

    def build_list(
        self,
        threads: List[Any],
        collected_thread_ids: Set[int],
        channel_to_virtual: Optional[Dict[int, List[str]]] = None,
    ) -> List[ThreadDetail]:
        """批量构建 ThreadDetail 列表。"""
        if channel_to_virtual is None:
            channel_to_virtual = self._global_virtual_tags_map

        return [
            self.build(t, collected_thread_ids, channel_to_virtual) for t in threads
        ]
