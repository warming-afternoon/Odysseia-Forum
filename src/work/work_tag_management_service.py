"""统一身份下的全站标签管理与书单标签治理。"""

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from core.tag_pool_cache_service import TagPoolCacheService
from core.tag_data_repository import TagDataRepository
from identity.business_identity import resolve_business_id
from shared.redis_client import RedisManager
from dto.events.tag_command import TagCommand
from dto.work.work_command_qo import WorkCommandQo
from tag.custom_tag_service import CustomTagService
from shared.tag_error import TagError
from models.tag_notification_task import TagNotificationTask


class WorkTagManagementService:
    """复用既有标签事务规则，权限始终使用已验证的当前身份。"""

    def __init__(self, sessions):
        """使用入口注入的数据库会话工厂。"""
        self.sessions = sessions

    async def dispatch(self, command: WorkCommandQo):
        """在同一事务执行标签维护，失败回滚且不信任请求中的身份。"""
        # 身份与可见范围只取入口核验结果，不读取客户端同名字段。
        principal = command.actor
        payload = dict(command.values)
        payload["_can_view_abyss"] = bool(principal["can_view_abyss"])
        payload["_include_platform_tags"] = True
        async with self.sessions() as session:
            service = CustomTagService(session, principal["config"])
            service.work_principal = principal
            service.unified_local_tags = True
            try:
                # 在写事务重新核验绑定，防止入口核验后切换账户。
                canonical = await resolve_business_id(
                    session, int(principal["discord_user_id"]), source="discord"
                )
                if str(canonical) != str(principal["id"]):
                    raise HTTPException(401, "账户绑定已变化，请重新登录")
                result = await service.dispatch(
                    TagCommand(
                        command.operation, int(principal["discord_user_id"]), payload
                    )
                )
                await session.commit()
            except IntegrityError as exc:
                raise HTTPException(409, "数据已变化，请刷新后重试") from exc
            except TagError as exc:
                await session.rollback()
                conflict_id = exc.detail.get("conflict_tag_id")
                if conflict_id:
                    async with self.sessions() as notices, notices.begin():
                        await TagDataRepository(notices, TagNotificationTask).add(
                            kind="conflict", tag_id=int(conflict_id)
                        )
                raise HTTPException(exc.status, exc.detail) from exc
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPException(422, "标签参数不完整或无效") from exc
        # 仅在事务成功后失效旧入口缓存，失败不污染页面读取。
        if command.operation == "manage" or (
            command.operation == "merge" and not payload.get("preview")
        ):
            try:
                redis = RedisManager.get_client()
            except RuntimeError:
                redis = None
            await TagPoolCacheService(redis).invalidate()
        return result
