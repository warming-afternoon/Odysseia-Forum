import logging

import discord

logger = logging.getLogger(__name__)


class ApplicationFormModal(discord.ui.Modal, title="Banner申请"):
    """Banner申请表单"""

    thread_id = discord.ui.TextInput(
        label="帖子ID",
        placeholder="请输入帖子的Thread ID（纯数字）",
        style=discord.TextStyle.short,
        required=True,
        min_length=17,
        max_length=20,
    )

    cover_image_url = discord.ui.TextInput(
        label="封面图链接（推荐16:9）",
        placeholder="https://...",
        style=discord.TextStyle.short,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        """表单提交 → 校验格式 → 分发 banner_form_submit 事件。"""
        await interaction.response.defer(ephemeral=True)

        thread_id_str = str(self.thread_id.value).strip()
        if not thread_id_str.isdigit():
            await interaction.followup.send("❌ 帖子ID必须是纯数字", ephemeral=True)
            return

        cover_url = str(self.cover_image_url.value).strip()

        interaction.client.dispatch(
            "banner_form_submit",
            interaction,
            int(thread_id_str),
            cover_url,
        )
