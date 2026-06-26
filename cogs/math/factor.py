import discord
from discord.ext import commands
from discord import app_commands
import sympy as sp
import time

MAX_DIGITS = 5000
TIME_LIMIT = 3.0  # 秒


class Factor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # =========================
    # Prefix command !factor / !pf
    # =========================
    @commands.command(name="factor", aliases=["pf"])
    async def factor(self, ctx, n: str):
        await self._factor_core(ctx.reply, n)

    # =========================
    # Slash command /factor
    # =========================
    @app_commands.command(
        name="factor",
        description="自然数を素因数分解します"
    )
    @app_commands.describe(
        n="素因数分解したい自然数"
    )
    async def factor_slash(
        self,
        interaction: discord.Interaction,
        n: str
    ):
        try:
            await interaction.response.defer()
        except Exception:
            pass
        await self._factor_core(
            lambda msg: interaction.followup.send(msg),
            n,
            interaction=interaction
        )

    # =========================
    # 共通処理
    # =========================
    async def _factor_core(self, send, n: str, interaction=None):
        if not n.isdigit():
            await send("自然数を入力してください")
            return

        if len(n) > MAX_DIGITS:
            await send(f"{MAX_DIGITS}桁以内の数のみ対応しています")
            return

        N = int(n)

        start = time.time()
        try:
            factors = sp.factorint(N)
        except Exception:
            await send("因数分解に失敗しました")
            return

        elapsed = time.time() - start
        if elapsed > TIME_LIMIT:
            await send(f"計算時間制限（{TIME_LIMIT}秒）を超えました")
            return

        result = " × ".join(
            f"{p}^{e}" if e > 1 else str(p)
            for p, e in factors.items()
        )

        await send(f"```tex\n{N} = {result}\n```")


async def setup(bot):
    print("factor.py cog loaded")
    await bot.add_cog(Factor(bot))
