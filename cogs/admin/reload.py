from discord.ext import commands
from discord import app_commands
import discord
import os


OK_EMOJI = "✅"
NG_EMOJI = "❌"


class Reload(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # =========================
    # ?? Embed
    # =========================
    def build_result_embed(self, success, failed):
        embed = discord.Embed(
            title="再読み込み結果",
            color=discord.Color.green() if not failed else discord.Color.orange()
        )

        if success:
            embed.add_field(
                name="成功",
                value="\n".join(f"`{c}`" for c in success),
                inline=False
            )

        if failed:
            embed.add_field(
                name="失敗",
                value="\n".join(
                    f"`{c}` → `{err}`" for c, err in failed.items()
                ),
                inline=False
            )

        embed.set_footer(text=f"成功: {len(success)} / 失敗: {len(failed)}")
        return embed

    # =========================
    # cogs ???
    # =========================
    def iter_cogs(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        cogs_root = os.path.dirname(base_dir)  # cogs/

        for root, _, files in os.walk(cogs_root):
            for file in files:
                if not file.endswith(".py"):
                    continue
                if file.startswith("_"):
                    continue

                full = os.path.join(root, file)
                rel = os.path.relpath(full, cogs_root)

                # cogs/math/diff.py -> cogs.math.diff
                module = "cogs." + rel.replace(os.sep, ".")[:-3]
                yield module

    def iter_cogs_in_folder(self, folder: str):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        cogs_root = os.path.dirname(base_dir)  # cogs/
        target = os.path.join(cogs_root, folder)
        if not os.path.isdir(target):
            return []

        modules = []
        for root, _, files in os.walk(target):
            for file in files:
                if not file.endswith(".py"):
                    continue
                if file.startswith("_"):
                    continue

                full = os.path.join(root, file)
                rel = os.path.relpath(full, cogs_root)
                module = "cogs." + rel.replace(os.sep, ".")[:-3]
                modules.append(module)

        return modules

    # =========================
    # ?? reload
    # =========================
    async def reload_all(self):
        success = []
        failed = {}

        for cog in self.iter_cogs():
            try:
                await self.bot.reload_extension(cog)
                success.append(cog)
            except Exception as e:
                failed[cog] = str(e)

        return success, failed

    # =========================
    # ???? reload
    # =========================
    async def reload_folder(self, folder: str):
        success = []
        failed = {}

        modules = self.iter_cogs_in_folder(folder)
        if not modules:
            return [], {folder: "フォルダが見つかりません"}

        for cog in modules:
            try:
                await self.bot.reload_extension(cog)
                success.append(cog)
            except Exception as e:
                failed[cog] = str(e)

        return success, failed

    # =========================
    # ?? reload
    # =========================
    async def reload_one(self, cog: str):
        try:
            await self.bot.reload_extension(f"cogs.{cog}")
            return [f"cogs.{cog}"], {}
        except Exception as e:
            return [], {f"cogs.{cog}": str(e)}

    # =========================
    # ??
    # =========================
    def list_cogs(self):
        return sorted(self.iter_cogs())

    # =========================
    # Prefix
    # =========================
    @commands.command(name="reload")
    async def reload(self, ctx, cog: str | None = None):
        if cog == "list":
            items = self.list_cogs()
            await ctx.reply("\n".join(f"`{c}`" for c in items))
            return

        # ??????
        if cog and os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", cog)):
            success, failed = await self.reload_folder(cog)
            embed = self.build_result_embed(success, failed)
            await ctx.reply(embed=embed)
            return

        # ??
        if cog:
            success, failed = await self.reload_one(cog)
            embed = self.build_result_embed(success, failed)
            await ctx.reply(embed=embed)
            return

        # ??????
        confirm = await ctx.reply(
            "⚠️ **全ての Cog を再読み込みします**\n"
            "10秒以内に `ok` と返信してください"
        )

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            msg = await self.bot.wait_for("message", timeout=10, check=check)
            if msg.content.lower() != "ok":
                await confirm.add_reaction(NG_EMOJI)
                return

            success, failed = await self.reload_all()
            embed = self.build_result_embed(success, failed)

            await confirm.add_reaction(OK_EMOJI)
            await ctx.reply(embed=embed)

        except TimeoutError:
            await confirm.add_reaction(NG_EMOJI)

    # =========================
    # Slash
    # =========================
    @app_commands.command(
        name="reload",
        description="Cog を再読み込みします（管理者のみ）"
    )
    @app_commands.describe(
        cog="例: math.diff / item.inventory / admin"
    )
    async def reload_slash(
        self,
        interaction: discord.Interaction,
        cog: str
    ):
        if cog == "list":
            items = self.list_cogs()
            await interaction.response.send_message(
                "\n".join(f"`{c}`" for c in items),
                ephemeral=True
            )
            return

        # ??????
        if os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", cog)):
            success, failed = await self.reload_folder(cog)
            embed = self.build_result_embed(success, failed)
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # ??
        success, failed = await self.reload_one(cog)
        embed = self.build_result_embed(success, failed)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(Reload(bot))
    print("reload.py cog loaded")
