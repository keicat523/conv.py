import re

import sympy as sp


# LaTeX関数表記をsympify可能な文字列へ戻す
def _latex_to_sympy_text(text: str) -> str:
    s = text
    s = s.replace(r"\sin", "sin")
    s = s.replace(r"\cos", "cos")
    s = s.replace(r"\tan", "tan")
    s = s.replace(r"\sqrt", "sqrt")
    s = s.replace(r"\log", "log")
    s = s.replace("{", "(").replace("}", ")")
    return s


# 入力文字列をSymPy向けに正規化
def to_sympy_input(text: str) -> str:
    s = text
    s = s.replace("\u03c0", "pi")
    s = s.replace("\u221e", "oo")

    # 関数名の入力ゆれをLaTeXコマンドへ統一
    s = re.sub(r"(?<!\\)\bsin\b", r"\\sin", s)
    s = re.sub(r"(?<!\\)\bcos\b", r"\\cos", s)
    s = re.sub(r"(?<!\\)\btan\b", r"\\tan", s)
    s = re.sub(r"(?<!\\)\bsqrt\b", r"\\sqrt", s)
    s = re.sub(r"(?<!\\)\blog\b", r"\\log", s)
    s = s.replace("\u221a", r"\sqrt")
    return s


# 文字列/式を見やすい表示へ整形
def pretty(expr) -> str:
    # 文字列ならまずSymPy式へ変換を試みる
    if isinstance(expr, str):
        src = to_sympy_input(expr)
        try:
            obj = sp.sympify(_latex_to_sympy_text(src))
            s = sp.sstr(obj)
        except Exception:
            s = _latex_to_sympy_text(src)
    else:
        s = sp.sstr(expr)

    s = s.replace("oo", "Inf")
    s = re.sub(r"\bpi\b", "\u03c0", s)
    s = s.replace("**", "^")
    s = re.sub(r"sqrt\(([a-zA-Z0-9])\)", lambda m: "\u221a" + m.group(1), s)
    s = re.sub(r"sqrt\(([^()]+)\)", lambda m: "\u221a(" + m.group(1) + ")", s)
    return s
