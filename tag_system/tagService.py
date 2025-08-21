import logging
from collections import defaultdict
from typing import List, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from tag_system.repository import TagSystemRepository

logger = logging.getLogger(__name__)


class TagService:
    """
    一个封装了标签数据访问和缓存的服务
    """

    def __init__(self, session: AsyncSession):
        self.repo = TagSystemRepository(session)
        self._id_to_name: Dict[int, str] = {}
        self._name_to_ids: Dict[str, List[int]] = defaultdict(list)
        self._unique_tag_names: List[str] = []

    async def build_cache(self):
        """
        从数据库加载所有标签，并构建/重建缓存。
        这应该在机器人启动时调用。
        """
        logger.info("Building tag cache...")
        all_tags = await self.repo.get_all_tags()
        self._id_to_name.clear()
        self._name_to_ids.clear()

        temp_name_to_ids = defaultdict(list)
        for tag in all_tags:
            self._id_to_name[tag.id] = tag.name
            temp_name_to_ids[tag.name].append(tag.id)

        self._name_to_ids = dict(temp_name_to_ids)
        self._unique_tag_names = sorted(self._name_to_ids.keys())
        logger.info(
            f"Tag cache built. Found {len(all_tags)} tags, {len(self._unique_tag_names)} unique names."
        )

    def update_cached_tag(self, tag_id: int, old_name: str, new_name: str):
        """
        精确地更新缓存中的单个标签信息。
        这应该在检测到标签名称变更后调用。
        """
        logger.info(
            f"Updating cached tag: id={tag_id}, old_name='{old_name}', new_name='{new_name}'"
        )
        # 更新 id -> name 映射
        self._id_to_name[tag_id] = new_name

        # 更新 name -> ids 映射
        # 从旧名称的列表中移除
        if old_name in self._name_to_ids and tag_id in self._name_to_ids[old_name]:
            self._name_to_ids[old_name].remove(tag_id)
            # 如果旧名称下没有其他id了，就删除这个键
            if not self._name_to_ids[old_name]:
                del self._name_to_ids[old_name]

        # 添加到新名称的列表中
        if new_name not in self._name_to_ids:
            self._name_to_ids[new_name] = []
        self._name_to_ids[new_name].append(tag_id)

        # 重建唯一名称列表
        self._unique_tag_names = sorted(self._name_to_ids.keys())
        logger.info("Tag cache updated successfully.")

    def get_name_by_id(self, tag_id: int) -> str | None:
        """从缓存中通过ID获取标签名称。"""
        return self._id_to_name.get(tag_id)

    def get_ids_by_name(self, tag_name: str) -> List[int]:
        """从缓存中通过名称获取所有对应的标签ID。"""
        return self._name_to_ids.get(tag_name, [])

    def get_all_tag_details(self) -> Dict[str, List[int]]:
        """获取所有标签名称及其对应的ID列表。"""
        return self._name_to_ids

    def get_unique_tag_names(self) -> List[str]:
        """获取所有去重并排序后的标签名称列表。"""
        return self._unique_tag_names
