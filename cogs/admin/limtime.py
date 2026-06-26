import discord
from discord import app_commands
from discord.ext import commands

from utils.timeout_manager import set_menu_timeout_seconds, set_timeout_seconds


# 基本メッセージ
USAGE_TEXT = "使用方法: c!limtime <menu/gen> 数値"
VALUE_ERROR_TEXT = "数値は 0 より大きい正の数で指定してください"


class LimTime(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # 数値入力バリデーション
    def _parse_seconds(self, raw: str | None) -> float | None:
        if raw is None:
            return None
        try:
            value = float(raw)
        except Exception:
            return None
        if value <= 0:
            return None
        return value

    # prefix/slash 共通の解析・設定処理
    async def _apply_limit(self, send_func, target: str | None, seconds_raw: str | None, *, ephemeral: bool):
        if target is None or seconds_raw is None:
            await send_func(USAGE_TEXT, ephemeral=ephemeral)
            return

        target = target.lower().strip()
        value = self._parse_seconds(seconds_raw)
        if value is None:
            await send_func(VALUE_ERROR_TEXT, ephemeral=ephemeral)
            return

        if target == "gen":
            set_timeout_seconds(value)
            await send_func(
                f"一般コマンドの実行時間制限を {value:g} 秒に設定しました",
                ephemeral=ephemeral,
            )
            return

        if target == "menu":
            set_menu_timeout_seconds(value)
            await send_func(
                f"メニューコマンドの実行時間制限を {value:g} 秒に設定しました",
                ephemeral=ephemeral,
            )
            return

        await send_func(USAGE_TEXT, ephemeral=ephemeral)

    # Prefixコマンド
    @commands.command(name="limtime", aliases=["limt"])
    async def limtime_cmd(self, ctx, target: str | None = None, seconds: str | None = None):
        await self._apply_limit(ctx.reply, target, seconds, ephemeral=False)

    # Slashコマンド
    @app_commands.command(
        name="limtime",
        description="実行時間制限を設定します",
    )
    @app_commands.describe(
        target="menu または gen",
        seconds="0より大きい秒数",
    )
    async def limtime_slash(
        self,
        interaction: discord.Interaction,
        target: str | None = None,
        seconds: str | None = None,
    ):
        async def send(msg: str, *, ephemeral: bool = True):
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(msg, ephemeral=ephemeral)

        await self._apply_limit(send, target, seconds, ephemeral=True)


async def setup(bot):
    await bot.add_cog(LimTime(bot))
    print("limtime.py cog loaded")
