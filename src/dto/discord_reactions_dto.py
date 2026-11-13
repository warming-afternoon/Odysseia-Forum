"""Discord 首帖完整反应的无会话传输对象。"""

from pydantic import BaseModel, Field


class DiscordReactionsDTO(BaseModel):
    """保留表情身份和完整计数，不把旧摘要数字伪装成表情。"""

    reactions: dict[str, int] = Field(default_factory=dict)
    """稳定表情键对应的当前计数。"""

    reaction_emojis: dict[str, dict] = Field(default_factory=dict)
    """原生表情 ID、名称和动画元数据。"""

    reaction_count: int = 0
    """最高单表情计数，空列表为零。"""

    @classmethod
    def from_discord(cls, reactions):
        """一次遍历 Discord 消息反应，供完整同步和批次刷新共用。"""
        counts, emojis = {}, {}
        for reaction in reactions:
            identifier = getattr(reaction.emoji, "id", None)
            name = getattr(reaction.emoji, "name", None) or str(reaction.emoji)
            key = "custom:" + str(identifier) if identifier else "unicode:" + name
            counts[key] = int(reaction.count)
            emojis[key] = {
                "id": str(identifier) if identifier else None,
                "name": name,
                "animated": bool(getattr(reaction.emoji, "animated", False)),
            }
        return cls(
            reactions=counts, reaction_emojis=emojis,
            reaction_count=max(counts.values(), default=0),
        )
