from dataclasses import dataclass

from dto.thread_update_dto import ThreadUpdateDTO
from dto.viewer_flag_map import ViewerFlagMap


@dataclass(frozen=True, slots=True)
class ThreadPresentationContext:
    """保存批量构建作品响应所需的附加数据。"""

    viewer_flags: ViewerFlagMap
    latest_updates: dict[int, ThreadUpdateDTO]

