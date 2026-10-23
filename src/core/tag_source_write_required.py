class TagSourceWriteRequired(Exception):
    """来源需要写入，调用方须退出共享锁事务后用独占事务重试。"""
