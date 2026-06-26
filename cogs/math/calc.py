import discord
from discord.ext import commands
from discord import app_commands
import re

from utils.sympy_runner import run_sympy_local

LATEX_RE = re.compile(r"\$(.+?)\$")


class Calc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ====================
    # Prefix c!calc
    # ====================
    @commands.command(name="calc")
    async def calc_cmd(self, ctx, *, text: str | None = None):
        # ★ 引数なし対策
        if not text:
            await ctx.reply("❌ 使い方: `c!calc $式$`")
            return

        reply = await self._calc_core(text)

        if len(reply) >= 2000:
            reply = "❌ 計算結果が文字数上限を超過しました"

        await ctx.reply(reply)

    # ====================
    # Slash /calc
    # ====================
    @app_commands.command(
        name="calc",
        description="数式を計算します（LaTeX形式）"
    )
    @app_commands.describe(
        expr="計算する式（例: x^2 + 3x）"
    )
    async def calc_slash(
        self,
        interaction: discord.Interaction,
        expr: str
    ):
        try:
            await interaction.response.defer()
        except Exception:
            pass
        reply = await self._calc_core(f"${expr}$")

        if len(reply) >= 2000:
            reply = "❌ 計算結果が文字数上限を超過しました"

        await interaction.followup.send(reply)

    # ====================
    # 共通処理（★ 非同期 & 3秒制限）
    # ====================
    async def _calc_core(self, text: str) -> str:
        matches = LATEX_RE.findall(text)

        if not matches:
            return "❌ `$...$` の形で数式を入力してください"

        results = []

        for latex_expr in matches:
            result = await run_sympy_local(
                mode="calc",
                expr=latex_expr
            )

            # ⏱ タイムアウト・エラーはそのまま表示
            if result.startswith(("⏱", "❌")):
                results.append(result)
                continue

            results.append(
                f"```tex\n{latex_expr} =\n{result}\n```"
            )

        return "\n".join(results)


async def setup(bot):
    await bot.add_cog(Calc(bot))
    print("calc.py cog loaded")
