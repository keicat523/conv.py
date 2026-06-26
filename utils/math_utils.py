import sympy as sp


def get_variable(expr, var_name: str | None):
    """
    var_name があればそれを使う
    なければ expr に含まれる変数から選ぶ
    なければ x
    """
    if var_name:
        return sp.Symbol(var_name)

    symbols = list(expr.free_symbols)
    if len(symbols) == 1:
        return symbols[0]

    return sp.Symbol("x")
