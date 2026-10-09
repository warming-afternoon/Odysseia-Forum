from dataclasses import dataclass

from banner.dto.banner_review_application import BannerReviewApplication


@dataclass(frozen=True)
class BannerApprovalResult:
    """批准申请及同批自动拒绝申请的事务结果。"""

    application: BannerReviewApplication
    """本次审核通过的申请快照，可在事务提交和会话关闭后使用"""

    entered_carousel: bool
    """是否直接加入轮播；False 表示已通过审核并加入等待队列"""

    auto_rejected: list[BannerReviewApplication]
    """同一事务内自动拒绝的其他待审核申请快照，不含已审核历史或其他申请人的申请"""
