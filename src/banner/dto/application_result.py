from dataclasses import dataclass
from typing import Optional

from models import BannerApplication
from dto.thread_dto import ThreadDTO


@dataclass
class ApplicationResult:
    """申请结果"""

    success: bool
    message: str
    application: Optional[BannerApplication] = None
    thread: Optional[ThreadDTO] = None
