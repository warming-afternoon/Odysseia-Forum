"""显式启用 Fluxer，旧部署默认保持 Discord 行为。"""

import os


def is_fluxer_mode() -> bool:
    """只接受显式配置，避免部分部署误切身份空间。"""
    mode = os.environ.get("FORUM_AUTH_MODE", "legacy").strip().lower()
    if mode not in {"legacy", "fluxer"}:
        raise RuntimeError("FORUM_AUTH_MODE 必须为 legacy 或 fluxer")
    return mode == "fluxer"
