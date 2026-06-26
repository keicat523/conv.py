from discord.ext import commands
import discord
import re

from utils.user_memo import add_memo, remove_memo, get_memos

ID_RE = re.compile(r"\d{17,20}")


class UserMemo(commands.Cog):
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

    async def _resolve_user(self, ctx, text: str | None):
        if ctx.message.mentions:
            return ctx.message.mentions[0]
        if text:
            m = ID_RE.search(text)
            if m:
                return await self._fetch_user_by_id(int(m.group()))
        return None

    @commands.command(name="memo")
    async def memo_cmd(
        self,
        ctx: commands.Context,
        action: str | None = None,
        target: str | None = None,
        *,
        content: str | None = None
    ):
        if action not in ("add", "remove", "list") or target is None:
            await ctx.reply(
                "使い方: `c!memo add @user 内容` / `c!memo remove @user ID` / `c!memo list @user`"
            )
            return

        action = action.lower()
        user = await self._resolve_user(ctx, target)
        if user is None:
            await ctx.reply("ユーザーが見つかりません")
            return

        if action == "list":
            memos = get_memos(user.id)
            if not memos:
                await ctx.reply("メモはありません")
                return
            lines = [f"ID{i+1}: {m}" for i, m in enumerate(memos)]
            await ctx.reply("\n".join(lines))
            return

        if content is None:
            await ctx.reply(
                "使い方: `c!memo add @user 内容` / `c!memo remove @user ID`"
            )
            return

        if action == "add":
            memo_id = add_memo(user.id, content)
            await ctx.reply(f"追加しました: ID{memo_id}")
            return

        # remove
        try:
            memo_id = int(content)
        except Exception:
            await ctx.reply("IDは数値で指定してください")
            return

        ok = remove_memo(user.id, memo_id)
        if ok:
            await ctx.reply(f"削除しました: ID{memo_id}")
        else:
            await ctx.reply("そのIDはありません")


async def setup(bot):
    await bot.add_cog(UserMemo(bot))
    print("user_memo.py cog loaded")
