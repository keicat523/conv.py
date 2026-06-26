import re
from decimal import Decimal, ROUND_DOWN, localcontext

import discord
from discord import app_commands
import sympy as sp
from discord.ext import commands
from sympy.parsing.latex import parse_latex
from utils.pretty import to_sympy_input


LATEX_RE = re.compile(r"\$(.+?)\$")
DEFAULT_DIGITS = 6
MAX_DIGITS = 100


class Value(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Prefixコマンド: c!value
    @commands.command(name="value")
    async def value_cmd(self, ctx, *, text: str | None = None):
        if not text:
            await ctx.reply("使用例: `c!value $式$ [小数桁数]`")
            return

        parsed = self._parse_args(text)
        if isinstance(parsed, str):
            await ctx.reply(parsed)
            return

        latex_expr, digits = parsed
        result = self._calc_value(latex_expr, digits)
        await ctx.reply(result)

    # ====================
    # Slash /value
    # ====================
    @app_commands.command(
        name="value",
        description="数式を数値評価します"
    )
    @app_commands.describe(
        expr="数式 (例: 2+sqrt\{23\})",
        digits="表示する小数桁数 (省略可)"
    )
    async def value_slash(
        self,
        interaction: discord.Interaction,
        expr: str,
        digits: int | None = None
    ):
        try:
            await interaction.response.defer()
        except Exception:
            pass

        resolved_digits = self._validate_digits(digits)
        if isinstance(resolved_digits, str):
            await interaction.followup.send(resolved_digits, ephemeral=True)
            return

        normalized = to_sympy_input(expr)
        result = self._calc_value(normalized, resolved_digits)
        await interaction.followup.send(result)

    # 入力解析: `$式$` と任意の桁数
    def _parse_args(self, text: str):
        match = LATEX_RE.search(text)
        if not match:
            return "`$...$` 形式で式を指定してください。"

        latex_expr = match.group(1)
        rest = LATEX_RE.sub(" ", text, count=1).strip()
        tokens = [t for t in rest.split() if t]

        if len(tokens) > 1:
            return "使用方法: `c!value $式$ [小数桁数]`"

        if not tokens:
            return latex_expr, DEFAULT_DIGITS

        try:
            digits = int(tokens[0])
        except Exception:
            return "小数桁数は整数で指定してください。"

        resolved_digits = self._validate_digits(digits)
        if isinstance(resolved_digits, str):
            return resolved_digits

        return latex_expr, resolved_digits

    # 小数桁数の検証
    def _validate_digits(self, digits: int | None):
        if digits is None:
            return DEFAULT_DIGITS

        if digits < 0:
            return "小数桁数は0以上で指定してください。"

        if digits > MAX_DIGITS:
            return f"小数桁数は {MAX_DIGITS} 以下で指定してください。"

        return digits

    # 数式を数値化して、指定桁で切り捨て表示
    def _calc_value(self, latex_expr: str, digits: int) -> str:
        cleaned = latex_expr.replace(" ", "")
        try:
            expr = parse_latex(cleaned)
        except Exception:
            # LaTeXで失敗したら、ゆるい数式として解釈
            loose = cleaned
            loose = re.sub(r"sqrt\{([^{}]+)\}", r"sqrt(\1)", loose)
            loose = loose.replace("{", "(").replace("}", ")")
            loose = loose.replace("^", "**")
            try:
                expr = sp.sympify(loose, locals={"sqrt": sp.sqrt})
            except Exception as e:
                return f"❌ 計算エラー: {type(e).__name__}: {e}"

        try:
            value = sp.N(sp.simplify(expr), digits + 60)
        except Exception as e:
            return f"❌ 計算エラー: {type(e).__name__}: {e}"

        if getattr(value, "is_real", None) is False:
            return "❌ 実数に変換できない式です。"

        try:
            dec_value = Decimal(str(value))
        except Exception:
            return "❌ 数値変換に失敗しました。"

        quant = Decimal("1").scaleb(-digits)
        try:
            with localcontext() as ctx:
                ctx.prec = 2048
                truncated = dec_value.quantize(quant, rounding=ROUND_DOWN)
        except Exception:
            return "❌ 指定桁での変換に失敗しました。"

        out = format(truncated, "f")
        if "." in out:
            out = out.rstrip("0").rstrip(".")
        return out


async def setup(bot):
    await bot.add_cog(Value(bot))
    print("value.py cog loaded")
