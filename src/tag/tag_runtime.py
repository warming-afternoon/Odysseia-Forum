from models.tag_notification_task import TagNotificationTask

from sqlalchemy.exc import IntegrityError

from dto.events.tag_command import TagCommand
from shared.event_mediator import EventMediator
from shared.tag_error import TagError
from tag.custom_tag_service import CustomTagService


def create_tag_mediator(session_factory, config) -> EventMediator:
    """在程序组装入口注册标签事件，避免业务模块直接相互导入。"""
    mediator = EventMediator()

    async def handle(command):
        """在独立事务中处理事件并返回脱离会话的数据。"""
        try:
            async with session_factory() as session, session.begin():
                return await CustomTagService(session, config).dispatch(command)
        except TagError as exc:
            conflict_id = exc.detail.get("conflict_tag_id")
            if conflict_id:
                async with session_factory() as session, session.begin():
                    session.add(
                        TagNotificationTask(kind="conflict", tag_id=int(conflict_id))
                    )
            raise
        except IntegrityError as exc:
            raise TagError("concurrent_conflict", "数据已变化，请刷新后重试") from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise TagError(
                "invalid_request", "缺少操作所需参数或参数格式错误", 422
            ) from exc

    mediator.register(TagCommand, handle)
    return mediator
