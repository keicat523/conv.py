import io
import re

import discord
from discord.ext import commands
from discord import app_commands

import sympy as sp
from sympy.parsing.latex import parse_latex
from sympy.core.relational import (
    Relational,
    Equality,
    StrictLessThan,
    LessThan,
    StrictGreaterThan,
    GreaterThan,
)

try:
    import numpy as np
except Exception:
    np = None

# =========================
# Config
# =========================
LATEX_RE = re.compile(r"\$(.+?)\$")
DEFAULT_X = 5.0
DEFAULT_Y = 5.0
MAX_FILE_SIZE = 5 * 1024 * 1024


class Graph(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ====================
    # Prefix c!graph
    # ====================
    @commands.command(name="graph")
    async def graph_cmd(self, ctx, *, text: str | None = None):
        if not text:
            await ctx.reply("使い方: `c!graph $式$ [range]`")
            return

        parsed = self._parse_input(text)
        if isinstance(parsed, str):
            await ctx.reply(parsed)
            return

        latex_expr, x_max, y_max = parsed

        try:
            img_bytes = self._render_plot(latex_expr, x_max, y_max)
        except Exception as e:
            await ctx.reply(f"描画エラー: {type(e).__name__}: {e}")
            return

        if img_bytes is None:
            await ctx.reply("グラフを描画できませんでした")
            return

        if len(img_bytes) > MAX_FILE_SIZE:
            await ctx.reply("画像サイズが 5MB を超えたため送信できません")
            return

        file = discord.File(io.BytesIO(img_bytes), filename="graph.png")
        await ctx.reply(file=file)

    # ====================
    # Slash /graph
    # ====================
    @app_commands.command(
        name="graph",
        description="グラフを描画します（$...$ で式を指定）"
    )
    @app_commands.describe(
        expr="式（例: x^2 + y^2 = 4）",
        x_range="|x|,|y| の上限（省略時は 5）"
    )
    async def graph_slash(
        self,
        interaction: discord.Interaction,
        expr: str,
        x_range: float | None = None
    ):
        text = f"${expr}$"
        if x_range is not None:
            text = f"${expr}$ {x_range}"

        await interaction.response.send_message(
            embed=discord.Embed(description="描画中...")
        )
        msg = await interaction.original_response()

        parsed = self._parse_input(text)
        if isinstance(parsed, str):
            await msg.edit(content=parsed, embed=None)
            return

        latex_expr, x_max, y_max = parsed

        try:
            img_bytes = self._render_plot(latex_expr, x_max, y_max)
        except Exception as e:
            await msg.edit(content=f"描画エラー: {type(e).__name__}: {e}", embed=None)
            return

        if img_bytes is None:
            await msg.edit(content="グラフを描画できませんでした", embed=None)
            return

        if len(img_bytes) > MAX_FILE_SIZE:
            await msg.edit(content="画像サイズが 5MB を超えたため送信できません", embed=None)
            return

        file = discord.File(io.BytesIO(img_bytes), filename="graph.png")
        await msg.edit(content=None, embed=None, attachments=[file])

    def _parse_input(self, text: str):
        matches = LATEX_RE.findall(text)
        if not matches:
            return "`$...$` の形で式を指定してください"

        latex_expr = self._normalize_latex(matches[0])

        rest = LATEX_RE.sub(" ", text)
        nums = []
        for token in rest.split():
            try:
                nums.append(float(token))
            except ValueError:
                continue

        if len(nums) >= 1:
            x_max = abs(nums[0])
            y_max = abs(nums[0])
        else:
            x_max = DEFAULT_X
            y_max = DEFAULT_Y

        if x_max <= 0 or y_max <= 0:
            return "Range must be > 0"

        return latex_expr, x_max, y_max

    def _normalize_latex(self, latex_expr: str) -> str:
        expr = (
            latex_expr
            .replace("<=", " \\le ")
            .replace(">=", " \\ge ")
            .replace("≦", " \\le ")
            .replace("≤", " \\le ")
            .replace("≧", " \\ge ")
            .replace("≥", " \\ge ")
        )
        # Ensure spaces around \le and \ge to avoid parsing like \ley
        expr = re.sub(r"(\\le)(?=\\w)", r"\\le ", expr)
        expr = re.sub(r"(?<=\\w)(\\le)", r" \\le", expr)
        expr = re.sub(r"(\\ge)(?=\\w)", r"\\ge ", expr)
        expr = re.sub(r"(?<=\\w)(\\ge)", r" \\ge", expr)
        return expr

    def _extract_region_groups(self, latex_expr: str):
        groups = []
        i = 0
        n = len(latex_expr)
        while i < n:
            if latex_expr[i] == '{':
                depth = 1
                j = i + 1
                while j < n and depth > 0:
                    if latex_expr[j] == '{':
                        depth += 1
                    elif latex_expr[j] == '}':
                        depth -= 1
                    j += 1
                if depth == 0:
                    groups.append(latex_expr[i + 1:j - 1])
                    i = j
                    continue
            i += 1
        if not groups:
            return None
        return groups

    def _parse_inequality_groups(self, latex_expr: str):
        groups_raw = self._extract_region_groups(latex_expr)
        if groups_raw is None:
            expr = parse_latex(latex_expr)
            if isinstance(expr, Relational):
                return [[expr]]
            return None

        groups = []
        for g in groups_raw:
            parts = [p.strip() for p in g.split(',') if p.strip()]
            rels = []
            for part in parts:
                rel = parse_latex(part)
                if not isinstance(rel, Relational):
                    raise ValueError("Use inequalities or equalities only")
                rels.append(rel)
            if not rels:
                continue
            groups.append(rels)
        if not groups:
            return None
        return groups

    def _render_inequality(self, groups, x_max: float, y_max: float) -> bytes | None:
        if np is None:
            raise ValueError("numpy is required")

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        x = sp.Symbol("x")
        y = sp.Symbol("y")

        xs = np.linspace(-x_max, x_max, 400)
        ys = np.linspace(-y_max, y_max, 400)
        X, Y = np.meshgrid(xs, ys)

        union_mask = np.zeros_like(X, dtype=bool)
        expr_list = []

        for rels in groups:
            group_mask = np.ones_like(X, dtype=bool)
            has_ineq = False
            for rel in rels:
                op = rel.rel_op
                expr_list.append(self._rel_to_str(rel))
                lhs = rel.lhs
                rhs = rel.rhs
                val_expr = lhs - rhs
                f = sp.lambdify((x, y), val_expr, "numpy")
                Z = f(X, Y)
                Z = np.array(Z, dtype=np.complex128)
                ok = np.isfinite(Z) & np.isreal(Z)
                Z = np.where(ok, np.real(Z), np.nan)

                if op == '==':
                    continue
                has_ineq = True
                if op == '<':
                    mask = Z < 0
                elif op == '<=':
                    mask = Z <= 0
                elif op == '>':
                    mask = Z > 0
                elif op == '>=':
                    mask = Z >= 0
                else:
                    raise ValueError("Unsupported operator")

                group_mask &= mask

            if has_ineq:
                union_mask |= group_mask

        fig = plt.figure(figsize=(6, 6))
        ax = fig.add_subplot(1, 1, 1)
        if expr_list:
            ax.set_title(",".join(expr_list), fontsize=9)
        if np.any(union_mask):
            ax.contourf(
                X, Y, union_mask.astype(int),
                levels=[0.5, 1],
                colors=["#1f77b4"],
                alpha=0.3,
                zorder=2
            )

        # Draw boundaries for each inequality / equality
        for rels in groups:
            for rel in rels:
                val_expr = rel.lhs - rel.rhs
                f = sp.lambdify((x, y), val_expr, "numpy")
                Z = f(X, Y)
                Z = np.array(Z, dtype=np.complex128)
                ok = np.isfinite(Z) & np.isreal(Z)
                Z = np.where(ok, np.real(Z), np.nan)
                try:
                    ax.contour(
                        X, Y, Z,
                        levels=[0],
                        colors="#1f77b4",
                        linewidths=1.5,
                        zorder=3
                    )
                except Exception:
                    pass
        ax.set_xlim(-x_max, x_max)
        ax.set_ylim(-y_max, y_max)
        ax.axhline(0, color="#999999", linewidth=1, zorder=0)
        ax.axvline(0, color="#999999", linewidth=1, zorder=0)
        ax.grid(True, color="#dddddd", linewidth=0.8, zorder=0)

        for dpi in (150, 120, 100, 80, 60):
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
            data = buf.getvalue()
            if len(data) <= MAX_FILE_SIZE:
                plt.close(fig)
                return data

        plt.close(fig)
        return data

    def _rel_to_str(self, rel) -> str:
        if isinstance(rel, Equality):
            left = sp.sstr(rel.lhs)
            right = sp.sstr(rel.rhs)
            s = f"{left}={right}"
        else:
            s = sp.sstr(rel)
            s = s.replace("==", "=")
        s = s.replace("**", "^").replace(" ", "")
        return s

    def _render_plot(self, latex_expr: str, x_max: float, y_max: float) -> bytes | None:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        x = sp.Symbol("x")
        y = sp.Symbol("y")
        groups = self._parse_inequality_groups(latex_expr)
        if groups is not None:
            return self._render_inequality(groups, x_max, y_max)

        expr = parse_latex(latex_expr)
        if isinstance(expr, sp.Equality):
            expr = expr.lhs - expr.rhs

        xs = None
        ys = None

        if np is not None:
            # Implicit function: f(x, y) = 0
            if y in expr.free_symbols:
                xs = np.linspace(-x_max, x_max, 400)
                ys = np.linspace(-y_max, y_max, 400)
                X, Y = np.meshgrid(xs, ys)
                f = sp.lambdify((x, y), expr, "numpy")
                Z = f(X, Y)

                Z = np.array(Z, dtype=np.complex128)
                Z = np.where(np.isfinite(Z) & np.isreal(Z), np.real(Z), np.nan)

                fig = plt.figure(figsize=(6, 6))
                ax = fig.add_subplot(1, 1, 1)
                ax.contour(X, Y, Z, levels=[0], colors="#1f77b4", zorder=3)
                ax.set_xlim(-x_max, x_max)
                ax.set_ylim(-y_max, y_max)
                ax.axhline(0, color="#999999", linewidth=1)
                ax.axvline(0, color="#999999", linewidth=1)
                ax.grid(True, color="#dddddd", linewidth=0.8)

                for dpi in (150, 120, 100, 80, 60):
                    buf = io.BytesIO()
                    fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
                    data = buf.getvalue()
                    if len(data) <= MAX_FILE_SIZE:
                        plt.close(fig)
                        return data

                plt.close(fig)
                return data

            xs = np.linspace(-x_max, x_max, 1000)
            f = sp.lambdify(x, expr, "numpy")
            ys = f(xs)

            ys = np.array(ys, dtype=np.complex128)
            mask = np.isfinite(ys) & np.isreal(ys)
            xs = xs[mask]
            ys = np.real(ys[mask])
        else:
            f = sp.lambdify(x, expr, "math")
            xs_list = [(-x_max + (2 * x_max) * i / 999) for i in range(1000)]
            ys_list = []
            for val in xs_list:
                try:
                    yv = f(val)
                except Exception:
                    yv = None
                ys_list.append(yv)

            xs = []
            ys = []
            for xv, yv in zip(xs_list, ys_list):
                if isinstance(yv, (int, float)):
                    xs.append(xv)
                    ys.append(float(yv))

        if xs is None or ys is None or len(xs) == 0:
            return None

        fig = plt.figure(figsize=(6, 6))
        ax = fig.add_subplot(1, 1, 1)
        ax.plot(xs, ys, color="#1f77b4", linewidth=2, zorder=3)
        ax.set_xlim(-x_max, x_max)
        ax.set_ylim(-y_max, y_max)
        ax.axhline(0, color="#999999", linewidth=1, zorder=0)
        ax.axvline(0, color="#999999", linewidth=1, zorder=0)
        ax.grid(True, color="#dddddd", linewidth=0.8, zorder=0)

        for dpi in (150, 120, 100, 80, 60):
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
            data = buf.getvalue()
            if len(data) <= MAX_FILE_SIZE:
                plt.close(fig)
                return data

        plt.close(fig)
        return data


async def setup(bot):
    await bot.add_cog(Graph(bot))
    print("graph.py cog loaded")
