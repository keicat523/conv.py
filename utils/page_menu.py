import discord

import config
from utils.timeout_manager import get_menu_timeout_seconds


class PageMenuView(discord.ui.View):
    """Button paginator shared by menu commands."""

    def __init__(self, pages: list[discord.Embed], author_id: int):
        super().__init__(timeout=get_menu_timeout_seconds())
        self.pages = pages
        self.index = 0
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("このメニューを操作できるのは実行者だけです。", ephemeral=True)
            return False
        return True

    async def _show(self, interaction: discord.Interaction, index: int) -> None:
        self.index = index % len(self.pages)
        await interaction.response.edit_message(embed=self.pages[self.index], view=self)

    @discord.ui.button(emoji=config.FIRST_EMOJI, style=discord.ButtonStyle.secondary)
    async def first(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction, 0)

    @discord.ui.button(emoji=config.PREV_EMOJI, style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction, self.index - 1)

    @discord.ui.button(emoji=config.NEXT_EMOJI, style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction, self.index + 1)

    @discord.ui.button(emoji=config.LAST_EMOJI, style=discord.ButtonStyle.secondary)
    async def last(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show(interaction, len(self.pages) - 1)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
