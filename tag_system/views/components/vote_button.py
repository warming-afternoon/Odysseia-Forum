import discord
from ...repository import TagSystemRepository
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..vote_view import TagVoteView


class TagVoteButton(discord.ui.Button):
    """标签投票按钮"""
    def __init__(self, tag: discord.ForumTag, vote_value: int, row: int):
        self.tag = tag
        self.vote_value = vote_value
        
        label_prefix = "👍" if vote_value == 1 else "👎"
        style = discord.ButtonStyle.green if vote_value == 1 else discord.ButtonStyle.red
        
        super().__init__(
            label=f"{label_prefix} {tag.name}",
            style=style,
            row=row,
            custom_id=f"tag_vote:{tag.id}:{vote_value}"
        )

    async def callback(self, interaction: discord.Interaction):
        
        view: 'TagVoteView' = self.view
        if not view:
            return

        await interaction.response.defer(ephemeral=True)
        
        try:
            # 从 session_factory 创建临时的 session 和 repository
            async with view.session_factory() as session:
                
                repo = TagSystemRepository(session)
                await repo.record_tag_vote(
                    user_id=interaction.user.id,
                    thread_id=interaction.channel.id,
                    tag_id=self.tag.id,
                    vote_value=self.vote_value
                )
            await interaction.followup.send("您的评价已记录！", ephemeral=True)
        except Exception as e:
            print(f"记录标签投票时出错: {e}")
            await interaction.followup.send("处理您的评价时发生错误，请稍后再试。", ephemeral=True)