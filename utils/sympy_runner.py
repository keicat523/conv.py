import asyncio
import json
import re
import sys
from pathlib import Path

import sympy as sp
from sympy.parsing.latex import parse_latex

WORKER = Path(__file__).parent / "sympy_worker.py"


def _normalize_latex_expr(text: str) -> str:
    s = str(text)
    s = s.replace("\u03c0", r"\pi")
    s = s.replace("\u221a", r"\sqrt")
    s = re.sub(r"(?<!\\)\bsin\b", r"\\sin", s)
    s = re.sub(r"(?<!\\)\bcos\b", r"\\cos", s)
    s = re.sub(r"(?<!\\)\btan\b", r"\\tan", s)
    s = re.sub(r"(?<!\\)\bsqrt\b", r"\\sqrt", s)
    s = re.sub(r"(?<!\\)\blog\b", r"\\log", s)
    return s


def _normalize_sympify_text(text: str) -> str:
    s = str(text)
    s = s.replace("\u03c0", "pi")
    s = s.replace("\u221e", "oo")
    s = s.replace(r"\sin", "sin")
    s = s.replace(r"\cos", "cos")
    s = s.replace(r"\tan", "tan")
    s = s.replace(r"\sqrt", "sqrt")
    s = s.replace(r"\log", "log")
    s = s.replace("{", "(").replace("}", ")")
    return s


def _calc(
    mode: str,
    expr_latex: str,
    var_name: str | None,
    bounds: tuple[str, str] | None = None,
    at_value: str | None = None,
):
    expr = parse_latex(_normalize_latex_expr(expr_latex))

    if mode == "calc":
        return sp.simplify(expr)

    if mode == "diff":
        var = sp.Symbol(var_name) if var_name else list(expr.free_symbols)[0]
        diff_expr = sp.diff(expr, var)
        if at_value is not None:
            val = sp.sympify(_normalize_sympify_text(at_value))
            return diff_expr.subs(var, val)
        return diff_expr

    if mode in ("int", "integrate"):
        var = sp.Symbol(var_name) if var_name else list(expr.free_symbols)[0]
        if bounds is not None:
            lower, upper = bounds
            lower_val = sp.sympify(_normalize_sympify_text(lower))
            upper_val = sp.sympify(_normalize_sympify_text(upper))
            return sp.integrate(expr, (var, lower_val, upper_val))
        return sp.integrate(expr, var)

    raise ValueError("unknown mode")


async def run_sympy_local(
    mode: str,
    expr: str,
    var: str | None = None,
    bounds: tuple[str, str] | None = None,
    at_value: str | None = None,
) -> str:
    try:
        result = await asyncio.to_thread(_calc, mode, expr, var, bounds, at_value)
        return str(result)
    except Exception as e:
        return f"❌ 計算エラー: {type(e).__name__}: {e}"


async def run_sympy(
    mode: str,
    expr: str,
    var: str | None = None,
) -> str:
    payload = {
        "mode": mode,
        "expr": expr,
        "var": var,
    }

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(WORKER),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate(json.dumps(payload).encode())

        stderr_text = stderr.decode(errors="replace")
        if stderr_text.strip():
            return f"❌ sympy_worker error:\n```\n{stderr_text}\n```"

        stdout_text = stdout.decode(errors="replace").strip()
        if not stdout_text:
            return "❌ sympy_worker から出力が返ってきませんでした"

        return stdout_text

    except Exception as e:
        return f"❌ runner 側エラー: {e}"
