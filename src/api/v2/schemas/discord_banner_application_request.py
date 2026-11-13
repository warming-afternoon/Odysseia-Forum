"""旧平台推荐位申请输入。"""

from pydantic import Field
from api.v2.schemas.work_banner_application_request import WorkBannerApplicationRequest


class DiscordBannerApplicationRequest(WorkBannerApplicationRequest):
    """显式声明 Discord 来源，数字编号不猜测为 Work。"""

    thread_link: str = Field(min_length=1, max_length=250)
    """原生帖子或频道的链接与编号。"""
