from discord.ext import commands
from discord import app_commands
import discord
import sympy as sp
from sympy.parsing.latex import parse_latex
import re

from utils.pretty import pretty

# 前後の空白OK・非貪欲
LATEX_PATTERN = re.compile(r"^\s*\$(.+?)\$\s*$")


class Limit(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    # =========================
    # 極限の向かう先を解析
    # =========================
    def parse_limit_point(self, text: str):
        text = text.strip()

        if text.endswith("+"):
            return sp.sympify(text[:-1]), "+"
        if text.endswith("-"):
            return sp.sympify(text[:-1]), "-"

        if text in ("inf", "+inf"):
            return sp.oo, None
        if text == "-inf":
            return -sp.oo, None

        return sp.sympify(text), None

    # =========================
    # 共通処理
    # =========================
    async def _limit_logic(self, latex: str, var_name: str, target: str) -> str:
        m = LATEX_PATTERN.match(latex)
        if not m:
            raise ValueError("LaTeX must be wrapped with $...$")

        latex_src = m.group(1)

        # ★ parse_latex 安定化
        latex_src = latex_src.replace(r"\cdot", " ")

        expr = parse_latex(latex_src)
        var = sp.Symbol(var_name)

        point, direction = self.parse_limit_point(target)

        result = sp.limit(expr, var, point, dir=direction)

        return f"```tex\n{pretty(result)}\n```"

    # =========================
    # Prefix c!limit / c!lim
    # =========================
    @commands.command(name="limit", aliases=["lim"])
    async def limit(self, ctx, latex: str, var: str, target: str):
        try:
            msg = await self._limit_logic(latex, var, target)
            await ctx.reply(msg)
        except ValueError:
            await ctx.reply(
                "❌ 入力形式:\n"
                "`$式$ 変数 向かう先`\n"
                "例:\n"
                "`$1/x$ x 0+`\n"
                "`$sin(x)/x$ x pi`\n"
                "`$1/x$ x inf`"
            )
        except Exception as e:
            # ★ 本当のエラーを表示（デバッグ用）
            await ctx.reply(
                "❌ 計算中にエラーが発生しました\n"
                f"```py\n{type(e).__name__}: {e}\n```"
            )

    # =========================
    # Slash /limit
    # =========================
    @app_commands.command(name="limit", description="極限を計算します")
    async def limit_slash(
        self,
        interaction: discord.Interaction,
        latex: str,
        var: str,
        target: str
    ):
        try:
            await interaction.response.defer()
        except Exception:
            pass
        try:
            msg = await self._limit_logic(latex, var, target)
            await interaction.followup.send(msg)
        except ValueError:
            await interaction.followup.send(
                "❌ 入力例:\n"
                "`$1/x$ x 0+`\n"
                "`$sin(x)/x$ x pi`\n"
                "`$1/x$ x inf`",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                "❌ 計算中にエラーが発生しました\n"
                f"```py\n{type(e).__name__}: {e}\n```",
                ephemeral=True
            )

    # =========================
    # Prefix エラーハンドラ
    # =========================
    @limit.error
    async def limit_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(
                "❌ 入力形式が正しくありません\n"
                "**形式:** `$式$ 変数 向かう先`\n"
                "**例:**\n"
                "`$1/x$ x 0+`\n"
                "`$sin(x)/x$ x pi`\n"
                "`$1/x$ x inf`"
            )
            return

        if isinstance(error, commands.BadArgument):
            await ctx.reply("❌ 引数の形式が正しくありません")
            return

        raise error


async def setup(bot):
    await bot.add_cog(Limit(bot))
    print("limit.py cog loaded")
