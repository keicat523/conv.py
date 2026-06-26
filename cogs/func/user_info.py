from discord.ext import commands
from discord import app_commands
import discord
import re
from utils.prefix_manager import load_prefix, has_custom_prefix
from admin_ids import ADMIN_IDS
from utils.user_memo import get_memos

ID_RE = re.compile(r"\d{17,20}")


class UserInfo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _fetch_user_by_id(self, user_id: int):
        user = self.bot.get_user(user_id)
        if user:
            return user
        try:
            return await self.bot.fetch_user(user_id)
        except Exception:
            return None

    async def _resolve_user_from_text(self, ctx, text: str | None):
        if text is None:
            return ctx.author

        if ctx.message.mentions:
            return ctx.message.mentions[0]

        m = ID_RE.search(text)
        if m:
            return await self._fetch_user_by_id(int(m.group()))

        if ctx.guild:
            member = discord.utils.get(ctx.guild.members, name=text)
            if member:
                return member
            member = discord.utils.get(ctx.guild.members, display_name=text)
            if member:
                return member

        return None

    # =========================
    # Build Embed
    # =========================
    def build_embed(self, target: discord.abc.User, is_admin: bool):
        prefix = load_prefix(target.id)
        has_prefix = has_custom_prefix(target.id)

        embed = discord.Embed(
            title="ユーザー情報",
            color=discord.Color.green()
        )

        embed.set_thumbnail(url=target.display_avatar.url)

        embed.add_field(
            name="ユーザー名",
            value=target.name,
            inline=False
        )

        embed.add_field(
            name="ユーザーID",
            value=str(target.id),
            inline=False
        )

        prefix_value = f"`{prefix}`" if has_prefix else "未設定"
        embed.add_field(
            name="カスタムPrefix",
            value=prefix_value,
            inline=False
        )

        if is_admin:
            embed.add_field(
                name="管理者情報",
                value="管理者用情報を表示",
                inline=False
            )

            embed.add_field(
                name="アカウント作成日",
                value=target.created_at.strftime("%Y-%m-%d %H:%M"),
                inline=True
            )

            joined_at = getattr(target, "joined_at", None)
            embed.add_field(
                name="サーバー参加日",
                value=(
                    joined_at.strftime("%Y-%m-%d %H:%M")
                    if joined_at else "不明"
                ),
                inline=True
            )

            memos = get_memos(target.id)
            if memos:
                value = "\n".join(f"ID{i+1}: {m}" for i, m in enumerate(memos))
            else:
                value = "メモはありません"
            embed.add_field(
                name="管理者メモ",
                value=value,
                inline=False
            )

        return embed

    # =========================
    # Prefix command
    # =========================
    @commands.command(name="user_info",aliases=["ui"])
    async def user_info(self, ctx, *, member: str | None = None):
        target = await self._resolve_user_from_text(ctx, member)
        if target is None:
            await ctx.reply("ユーザーが見つかりません")
            return

        is_admin = ctx.author.id in ADMIN_IDS
        embed = self.build_embed(target, is_admin)
        await ctx.reply(embed=embed)

    # =========================
    # Slash command
    # =========================
    @app_commands.command(
        name="user_info",
        description="ユーザー情報を表示します"
    )
    @app_commands.describe(
        member="対象ユーザー",
        user_id="ユーザーID（さみ込み可）"
    )
    async def user_info_slash(
        self,
        interaction: discord.Interaction,
        member: discord.User | None = None,
        user_id: str | None = None
    ):
        target = member
        if target is None and user_id:
            m = ID_RE.search(user_id)
            if m:
                target = await self._fetch_user_by_id(int(m.group()))

        if target is None:
            target = interaction.user

        is_admin = interaction.user.id in ADMIN_IDS
        embed = self.build_embed(target, is_admin)
        await interaction.response.send_message(
            embed=embed,
            ephemeral=not is_admin
        )


async def setup(bot):
    print("user_info.py cog loaded")
    await bot.add_cog(UserInfo(bot))
