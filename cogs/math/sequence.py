import re

import discord
from discord.ext import commands
from discord import app_commands
import sympy as sp
from utils.pretty import pretty as pretty_expr

LATEX_RE = re.compile(r"\$(.+?)\$")


def _normalize_expr(expr: str) -> str:
    # basic normalization for a_n style to a(n)
    s = expr.replace(" ", "")
    s = s.replace("\\cdot", "*")
    s = s.replace("^", "**")

    # a_{n+2} -> a(n+2)
    s = re.sub(r"a_\{([^}]+)\}", r"a(\1)", s)
    # a_n -> a(n)
    s = re.sub(r"a_([a-zA-Z0-9]+)", r"a(\1)", s)

    # implicit multiplication: 2a(n+1) -> 2*a(n+1)
    s = re.sub(r"(\d)(a\()", r"\1*\2", s)
    s = re.sub(r"\)(a\()", r")*\1", s)
    return s


class Sequence(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="sequence", aliases=["seq"])
    async def sequence_cmd(self, ctx, *, text: str | None = None):
        if not text:
            await ctx.reply("構文が正しくありません。例: `c!sequence $a_1=1, a_2=2, a_{n+2}=a_n+2a_{n+1}$`")
            return

        matches = LATEX_RE.findall(text)
        if not matches:
            await ctx.reply("構文が正しくありません。例: `c!sequence $a_1=1, a_2=2, a_{n+2}=a_n+2a_{n+1}$`")
            return

        raw = matches[0]
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if not parts:
            await ctx.reply("構文が正しくありません。")
            return

        n = sp.symbols("n", integer=True)
        a = sp.Function("a")

        init = {}
        recur = None

        for p in parts:
            if "=" not in p:
                await ctx.reply("構文が正しくありません。")
                return
            left, right = [x.strip() for x in p.split("=", 1)]
            left_n = _normalize_expr(left)
            right_n = _normalize_expr(right)

            try:
                left_expr = sp.sympify(left_n, locals={"a": a, "n": n})
                right_expr = sp.sympify(right_n, locals={"a": a, "n": n})
            except Exception as e:
                await ctx.reply(f"解析エラー: {type(e).__name__}: {e}")
                return

            if left_expr.func == a and left_expr.args:
                idx = left_expr.args[0]
                if idx.is_Integer:
                    init[int(idx)] = right_expr
                    continue

            # recurrence: left must be a(n+k)
            recur = sp.Eq(left_expr, right_expr)

        if recur is None:
            await ctx.reply("漸化式が見つかりませんでした。")
            return

        if not init:
            await ctx.reply("初期条件が見つかりませんでした。")
            return

        try:
            sol = sp.rsolve(recur, a(n), init)
        except Exception as e:
            await ctx.reply(f"解けませんでした: {type(e).__name__}: {e}")
            return

        expr_str = self._format_equation(a(n), sp.simplify(sol))
        await ctx.reply(f"```\n{expr_str}\n```")

    @app_commands.command(
        name="sequence",
        description="数列の漸化式を解きます（$...$ の形式）"
    )
    @app_commands.describe(
        expr="例: a_1=1, a_2=2, a_{n+2}=a_n+2a_{n+1}"
    )
    async def sequence_slash(
        self,
        interaction: discord.Interaction,
        expr: str
    ):
        try:
            await interaction.response.defer()
        except Exception:
            pass
        text = f"${expr}$"

        matches = LATEX_RE.findall(text)
        if not matches:
            await interaction.followup.send(
                "構文が正しくありません。 例: a_1=1, a_2=2, a_{n+2}=a_n+2a_{n+1}",
                ephemeral=True
            )
            return

        raw = matches[0]
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if not parts:
            await interaction.followup.send("構文が正しくありません。", ephemeral=True)
            return

        n = sp.symbols("n", integer=True)
        a = sp.Function("a")

        init = {}
        recur = None

        for p in parts:
            if "=" not in p:
                await interaction.followup.send("構文が正しくありません。", ephemeral=True)
                return
            left, right = [x.strip() for x in p.split("=", 1)]
            left_n = _normalize_expr(left)
            right_n = _normalize_expr(right)

            try:
                left_expr = sp.sympify(left_n, locals={"a": a, "n": n})
                right_expr = sp.sympify(right_n, locals={"a": a, "n": n})
            except Exception as e:
                await interaction.followup.send(
                    f"解析エラー: {type(e).__name__}: {e}",
                    ephemeral=True
                )
                return

            if left_expr.func == a and left_expr.args:
                idx = left_expr.args[0]
                if idx.is_Integer:
                    init[int(idx)] = right_expr
                    continue

            recur = sp.Eq(left_expr, right_expr)

        if recur is None:
            await interaction.followup.send("漸化式が見つかりませんでした。", ephemeral=True)
            return

        if not init:
            await interaction.followup.send("初期条件が見つかりませんでした。", ephemeral=True)
            return

        try:
            sol = sp.rsolve(recur, a(n), init)
        except Exception as e:
            await interaction.followup.send(
                f"解けませんでした: {type(e).__name__}: {e}",
                ephemeral=True
            )
            return

        expr_str = self._format_equation(a(n), sp.simplify(sol))
        await interaction.followup.send(
            f"```\n{expr_str}\n```"
        )

    def _format_equation(self, lhs, rhs) -> str:
        left = pretty_expr(lhs)
        right = pretty_expr(rhs)
        return f"{left}={right}"


async def setup(bot):
    await bot.add_cog(Sequence(bot))
    print("sequence.py cog loaded")
