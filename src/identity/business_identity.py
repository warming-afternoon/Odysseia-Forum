"""统一业务身份和 Discord 机器人身份之间的显式转换。"""

from identity.identity_repository import IdentityRepository
from identity.runtime import is_fluxer_mode


async def resolve_business_id(session, user_id):
    """Fluxer 模式下将机器人传入的 Discord 整数 ID 映射到本地权威投影。"""
    if not is_fluxer_mode() or isinstance(user_id, str):
        return user_id
    if user_id is None or isinstance(user_id, bool) or user_id <= 0:
        raise ValueError("缺少有效的 Discord 用户身份")
    mappings = await IdentityRepository(session).discord_to_fluxer([user_id])
    resolved = mappings.get(str(user_id))
    if resolved is None:
        raise ValueError(f"Discord 用户 {user_id} 尚未映射到 Fluxer")
    return resolved


async def resolve_business_ids(session, user_ids):
    """批量解析机器人 ID，缺失映射时终止整个操作。"""
    if not is_fluxer_mode():
        return user_ids
    discord_ids = [value for value in user_ids if not isinstance(value, str)]
    mappings = (
        await IdentityRepository(session).discord_to_fluxer(discord_ids)
        if discord_ids
        else {}
    )
    resolved = []
    for value in user_ids:
        if isinstance(value, str):
            resolved.append(value)
        elif value and not isinstance(value, bool) and mappings.get(str(value)):
            resolved.append(mappings[str(value)])
        else:
            raise ValueError(f"Discord 用户 {value} 尚未映射到 Fluxer")
    return resolved


async def discord_id_for_business_id(session, user_id):
    """在可用时保留已验证的 Discord 审计 ID，原生账户返回空。"""
    if not is_fluxer_mode():
        return user_id
    mapping = await IdentityRepository(session).fluxer_to_discord([user_id])
    value = mapping.get(str(user_id))
    return int(value) if value else None
