from discord.ext import commands
from discord import app_commands
import discord
import re

from utils.pretty import pretty
from utils.sympy_runner import run_sympy_local

LATEX_RE = re.compile(r"\$(.+?)\$")


class Diff(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="diff")
    async def diff_cmd(self, ctx, *, text: str | None = None):
        if not text:
            await ctx.reply("使用例: `c!diff $式$ [変数] [値]`")
            return

        parsed = self._parse_args(text)
        if isinstance(parsed, str):
            await ctx.reply(parsed)
            return

        latex_expr, var_name, at_value = parsed
        reply = await self._diff_core(latex_expr, var_name, at_value)

        if len(reply) >= 2000:
            reply = "出力が長すぎます。"

        await ctx.reply(reply)

    @app_commands.command(name="diff", description="微分を計算します")
    @app_commands.describe(
        expr="式 (例: x^2 + 3x)",
        var="変数 (省略可)",
        value="代入する値 (省略可)",
    )
    async def diff_slash(
        self,
        interaction: discord.Interaction,
        expr: str,
        var: str | None = None,
        value: str | None = None,
    ):
        try:
            await interaction.response.defer()
        except Exception:
            pass

        reply = await self._diff_core(expr, var, value)

        if len(reply) >= 2000:
            reply = "出力が長すぎます。"

        await interaction.followup.send(reply)

    def _parse_args(self, text: str):
        match = LATEX_RE.search(text)
        if not match:
            return "`$...$` 形式で式を指定してください。"

        latex_expr = match.group(1)
        rest = LATEX_RE.sub(" ", text)
        tokens = [t for t in rest.split() if t]

        if len(tokens) >= 2:
            var_name = tokens[0]
            at_value = tokens[1]
        elif len(tokens) == 1:
            var_name = tokens[0]
            at_value = None
        else:
            var_name = None
            at_value = None

        return latex_expr, var_name, at_value

    async def _diff_core(
        self,
        latex_expr: str,
        var_name: str | None = None,
        at_value: str | None = None,
    ) -> str:
        result = await run_sympy_local(
            mode="diff",
            expr=latex_expr,
            var=var_name,
            at_value=at_value,
        )

        if result.startswith("❌"):
            return result

        return f"```tex\n{pretty(result)}\n```"


async def setup(bot):
    await bot.add_cog(Diff(bot))
    print("diff.py cog loaded")
