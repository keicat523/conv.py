from discord.ext import commands
from discord import app_commands
import discord
import re

from utils.pretty import pretty
from utils.sympy_runner import run_sympy_local

LATEX_RE = re.compile(r"\$(.*?)\$")


class Integrate(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="integrate", aliases=["int"])
    async def integrate_cmd(self, ctx, *, text: str | None = None):
        if not text:
            await ctx.reply("使用例: `c!integrate $式$ [変数] [下限] [上限]`")
            return

        parsed = self._parse_args(text)
        if isinstance(parsed, str):
            await ctx.reply(parsed)
            return

        latex_expr, var_name, bounds = parsed
        reply = await self._integrate_core(latex_expr, var_name, bounds)

        if len(reply) >= 2000:
            reply = "出力が長すぎます。"

        await ctx.reply(reply)

    @app_commands.command(name="integrate", description="積分を計算します")
    @app_commands.describe(
        expr="式 (例: x^2)",
        var="変数 (省略可)",
        lower="下限 (省略可)",
        upper="上限 (省略可)",
    )
    async def integrate_slash(
        self,
        interaction: discord.Interaction,
        expr: str,
        var: str | None = None,
        lower: str | None = None,
        upper: str | None = None,
    ):
        try:
            await interaction.response.defer()
        except Exception:
            pass

        if (lower is None) != (upper is None):
            await interaction.followup.send("下限と上限は両方指定してください。")
            return

        bounds = (lower, upper) if lower is not None and upper is not None else None
        reply = await self._integrate_core(expr, var, bounds)

        if len(reply) >= 2000:
            reply = "出力が長すぎます。"

        try:
            await interaction.followup.send(reply)
        except discord.errors.NotFound:
            pass

    def _parse_args(self, text: str):
        matches = LATEX_RE.findall(text)
        if not matches:
            return "`$...$` 形式で式を指定してください。"

        latex_expr = matches[0]
        rest = LATEX_RE.sub(" ", text)
        tokens = [t for t in rest.split() if t]

        if len(tokens) >= 3:
            var_name = tokens[0]
            bounds = (tokens[1], tokens[2])
        elif len(tokens) == 2:
            return "下限と上限は両方指定してください。"
        elif len(tokens) == 1:
            var_name = tokens[0]
            bounds = None
        else:
            var_name = None
            bounds = None

        return latex_expr, var_name, bounds

    async def _integrate_core(
        self,
        latex_expr: str,
        var_name: str | None = None,
        bounds: tuple[str, str] | None = None,
    ) -> str:
        result = await run_sympy_local(
            mode="integrate",
            expr=latex_expr,
            var=var_name,
            bounds=bounds,
        )

        if result.startswith("❌"):
            return result

        if bounds is not None:
            return f"```tex\n{pretty(result)}\n```"
        return f"```tex\n{pretty(result)} + C\n```"


async def setup(bot):
    await bot.add_cog(Integrate(bot))
    print("integrate.py cog loaded")
