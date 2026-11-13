"""增长事件容量及近似校准边界配置。"""

import os


class WorkGrowthPolicy:
    """清除去重记录前，必须已经拒收其可能覆盖的过期事件。"""

    def __init__(self):
        """限制配置范围，避免缩短去重期后允许旧事件重复累计。"""
        self.accept_days = int(os.getenv("WORK_GROWTH_ACCEPT_DAYS", "90"))
        self.receipt_days = int(os.getenv("WORK_GROWTH_RECEIPT_DAYS", "120"))
        self.payload_days = int(os.getenv("WORK_GROWTH_PAYLOAD_DAYS", "7"))
        self.cleanup_batches = int(os.getenv("WORK_GROWTH_CLEANUP_BATCHES", "10"))
        if not 1 <= self.cleanup_batches <= 50:
            raise ValueError("增长清理批次数必须为 1 至 50")
        self.quiet_seconds = int(os.getenv("WORK_GROWTH_QUIET_SECONDS", "120"))
        if not 1 <= self.accept_days <= 90:
            raise ValueError("增长接受窗口必须为 1 至 90 天")
        if self.receipt_days < self.accept_days + 8:
            raise ValueError("去重保留期必须比接受窗口至少长 8 天")
        if not 1 <= self.payload_days <= self.receipt_days:
            raise ValueError("原始事件保留期必须位于去重保留期内")
        if self.quiet_seconds < 120:
            raise ValueError("近似静默校准窗口不得短于 120 秒")
