from discord.ext import commands
from discord import app_commands, Interaction
import discord
import sympy as sp
from sympy.parsing.latex import parse_latex
import re

from utils.pretty import pretty

LATEX_PATTERN = re.compile(r"\$(.*?)\$")


# =========================
# 変数決定ユーティリティ
# =========================
def get_variable(expr, var_name: str | None = None):
    if var_name:
        return sp.Symbol(var_name)

    symbols = list(expr.free_symbols)
    if symbols:
        return symbols[0]

    return sp.Symbol("x")


class Solve(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # =========================
    # 共通ロジック
    # =========================
    def solve_core(self, latex_expr: str, var_name: str | None):
        # 方程式 or 式
        if "=" in latex_expr:
            left, right = latex_expr.split("=", 1)
            equation = parse_latex(left) - parse_latex(right)
        else:
            equation = parse_latex(latex_expr)

        var = get_variable(equation, var_name)

        # ===== 厳密解 =====
        try:
            sols = sp.solve(equation, var)
            if sols:
                sol_text = ", ".join(pretty(s) for s in sols)
                return f"{pretty(var)} = {sol_text}"
        except (NotImplementedError, ValueError):
            pass

        # ===== 数値解 =====
        try:
            num = sp.nsolve(equation, var, 0)
            return f"{pretty(var)} \\approx {pretty(num)}"
        except Exception:
            pass

        return None

    # =========================
    # Prefix c!solve
    # =========================
    @commands.command(name="solve")
    async def solve(self, ctx, *, text: str):
        matches = LATEX_PATTERN.findall(text)
        parts = text.split()

        if not matches:
            await ctx.reply("❌ 使い方: `c!solve $式$ [変数]`")
            return

        latex_expr = matches[0]

        var_name = None
        if len(parts) > 1 and parts[-1].isalpha():
            var_name = parts[-1]

        try:
            result = self.solve_core(latex_expr, var_name)
            if result:
                await ctx.reply(f"```tex\n{result}\n```")
            else:
                await ctx.reply("❌ 解が見つかりませんでした")
        except Exception:
            await ctx.reply("❌ 方程式が解けませんでした")

    # =========================
    # Slash /solve
    # =========================
    @app_commands.command(
        name="solve",
        description="方程式を解きます（LaTeX形式）"
    )
    @app_commands.describe(
        equation="式または方程式（例: x^2+1=0）",
        variable="変数（省略すると自動判定）"
    )
    async def solve_slash(
        self,
        interaction: Interaction,
        equation: str,
        variable: str | None = None
    ):
        try:
            await interaction.response.defer()
        except Exception:
            pass
        try:
            result = self.solve_core(equation, variable)
            if result:
                await interaction.followup.send(
                    f"```tex\n{result}\n```"
                )
            else:
                await interaction.followup.send(
                    "❌ 解が見つかりませんでした",
                    ephemeral=True
                )
        except Exception:
            await interaction.followup.send(
                "❌ 方程式が解けませんでした",
                ephemeral=True
            )


async def setup(bot):
    await bot.add_cog(Solve(bot))
    print("solve.py cog loaded")
