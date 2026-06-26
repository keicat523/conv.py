from discord.ext import commands
from discord import app_commands
import discord
from utils.prefix_manager import save_prefix, load_prefix


class Prefix(commands.Cog):
    def __init__(self, bot):
        self.bot = bot


    # =========================
    # Prefix コマンド（共通処理）
    # =========================
    async def _set_prefix(self, user_id: int, prefix: str):
        if len(prefix) > 5:
            return False, "prefix は5文字以内にしてください"

        save_prefix(user_id, prefix)
        return True, f"あなたの prefix を `{prefix}` に設定しました"

    # =========================
    # Prefix 版
    # =========================
    @commands.command(name="setprefix")
    async def setprefix(self, ctx, prefix: str):
        ok, msg = await self._set_prefix(ctx.author.id, prefix)
        await ctx.reply(msg)

    # =========================
    # Slash 版
    # =========================
    @app_commands.command(
        name="setprefix",
        description="自分専用のコマンド prefix を設定します"
    )
    @app_commands.describe(
        prefix="設定したい prefix（5文字以内）"
    )
    async def setprefix_slash(
        self,
        interaction: discord.Interaction,
        prefix: str
    ):
        ok, msg = await self._set_prefix(interaction.user.id, prefix)
        await interaction.response.send_message(msg, ephemeral=True)
    @setprefix.error
    async def setprefix_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                "❌ prefix が指定されていません\n"
                "**使い方:** `setprefix <prefix>`\n"
                "**例:** `setprefix !`"
            )
            return

        raise error

async def setup(bot):
    print("prefix.py cog loaded")
    await bot.add_cog(Prefix(bot))
