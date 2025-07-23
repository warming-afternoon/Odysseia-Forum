import discord
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from ..repository import TagSystemRepository
from .components.vote_button import TagVoteButton

class TagVoteView(discord.ui.View):
    """标签投票视图"""
    def __init__(self, tags: list[discord.ForumTag], session_factory: sessionmaker):
        super().__init__(timeout=180)
        self.session_factory = session_factory

        # 将标签按行分组，每行最多5个
        row_count = 0
        # 点赞按钮
        for i, tag in enumerate(tags):
            if i > 0 and i % 5 == 0:
                row_count += 1
            self.add_item(TagVoteButton(tag, vote_value=1, row=row_count))
        
        row_count += 1
        # 点踩按钮
        for i, tag in enumerate(tags):
            if i > 0 and i % 5 == 0:
                row_count += 1
            self.add_item(TagVoteButton(tag, vote_value=-1, row=row_count))