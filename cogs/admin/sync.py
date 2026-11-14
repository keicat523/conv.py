
import discord
from discord.ext import commands

import config
from admin_ids import ADMIN_IDS


class Sync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._syncing = False

    @commands.command(name="sync")
    async def sync_commands(self, ctx):
        # 管理者チェック
        if ctx.author.id not in ADMIN_IDS:
            await ctx.reply("❌ 管理者権限がありません")
            return

        # 二重実行防止
        if self._syncing:
            await ctx.reply("⚠️ 現在同期中です")
            return

        self._syncing = True

        try:
            guild_id = getattr(config, "GUILD_ID", None)

            if guild_id:
                guild = discord.Object(id=int(guild_id))

                # グローバルコマンドをギルドにコピー
                self.bot.tree.copy_global_to(guild=guild)

                # ギルドコマンドを同期
                synced = await self.bot.tree.sync(guild=guild)
                scope = f"ギルド（ID: {guild_id}）"

            else:
                # グローバルコマンドを同期
                synced = await self.bot.tree.sync()
                scope = "グローバル"

            await ctx.reply(
                f"✅ **スラッシュコマンドの同期が完了しました**\n"
                f"同期先: {scope}\n"
                f"同期件数: {len(synced)} 件"
            )

            print(
                f"[SYNC] {scope} / {len(synced)} commands synced",
                flush=True
            )

        except Exception as e:
            await ctx.reply(
                f"❌ **同期に失敗しました**\n"
                f"`{type(e).__name__}: {e}`"
            )

            print(
                f"[SYNC ERROR] {type(e).__name__}: {e}",
                flush=True
            )

        finally:
            self._syncing = False


async def setup(bot):
    await bot.add_cog(Sync(bot))
    print("sync.py cog loaded")
