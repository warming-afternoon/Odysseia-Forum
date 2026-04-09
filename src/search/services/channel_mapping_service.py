from typing import Dict, List, Optional

from search.dto.channel_mapping_resolution import ChannelMappingResolutionDTO


class ChannelMappingService:
    """处理频道与虚拟标签映射逻辑的服务"""

    def __init__(self, channel_mappings_config: Dict[int, List[Dict]]):
        self.channel_mappings = channel_mappings_config

    def resolve(
        self,
        channel_ids: Optional[List[int]],
        include_tags: List[str],
        exclude_tags: List[str],
    ) -> ChannelMappingResolutionDTO:
        """解析频道和标签映射，转换为实际的数据库查询参数"""
        result = ChannelMappingResolutionDTO(
            effective_channel_ids=list(channel_ids) if channel_ids else None,
            effective_include_tags=list(include_tags),
            effective_exclude_tags=list(exclude_tags),
            searched_ids=set(),
            has_mapping=False,
        )

        # 若无频道ID或非单一频道，直接返回
        if not result.effective_channel_ids or len(result.effective_channel_ids) != 1:
            return result

        origin_channel_id = result.effective_channel_ids[0]
        mappings = self.channel_mappings.get(origin_channel_id, [])
        if not mappings:
            return result

        result.has_mapping = True
        virtual_tag_set = {m["tag_name"] for m in mappings}
        mapping_tag_lookup = {m["tag_name"]: m for m in mappings}

        # 分离出正选/反选中的虚拟标签
        included_virtual = [
            tag for tag in result.effective_include_tags if tag in virtual_tag_set
        ]
        excluded_virtual = [
            tag for tag in result.effective_exclude_tags if tag in virtual_tag_set
        ]

        # 从有效标签列表中移除虚拟标签
        result.effective_include_tags = [
            tag for tag in result.effective_include_tags if tag not in virtual_tag_set
        ]
        result.effective_exclude_tags = [
            tag for tag in result.effective_exclude_tags if tag not in virtual_tag_set
        ]

        # 收集反选虚拟标签对应需要排除的源频道
        excluded_channels = set()
        for virtual_tag in excluded_virtual:
            excluded_channels.update(
                mapping_tag_lookup[virtual_tag].get("source_channel_ids", [])
            )

        if included_virtual:
            # 取正选虚拟标签对应的源频道交集
            channel_sets = []
            for virtual_tag in included_virtual:
                channel_sets.append(
                    set(mapping_tag_lookup[virtual_tag].get("source_channel_ids", []))
                )

            intersected = channel_sets[0]
            for channel_set in channel_sets[1:]:
                intersected &= channel_set

            intersected -= excluded_channels
            result.effective_channel_ids = list(intersected)
        else:
            # 若无正选虚拟标签，则取所有映射源频道的并集
            all_mapped = set()
            for mapping in mappings:
                all_mapped.update(mapping.get("source_channel_ids", []))

            all_mapped -= excluded_channels
            result.effective_channel_ids = [origin_channel_id] + list(all_mapped)

        result.searched_ids = set(result.effective_channel_ids)
        return result

    def get_channel_virtual_tags_map(
        self,
        origin_channel_id: int,
    ) -> Dict[int, List[str]]:
        """构建源频道ID到虚拟标签名称的映射字典"""
        channel_to_virtual = {}
        for mapping in self.channel_mappings.get(origin_channel_id, []):
            for source_channel_id in mapping.get("source_channel_ids", []):
                channel_to_virtual.setdefault(source_channel_id, []).append(
                    mapping["tag_name"]
                )
        return channel_to_virtual
