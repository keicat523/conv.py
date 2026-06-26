import sys
import json
from sympy import symbols, simplify, diff, integrate
from sympy.parsing.latex import parse_latex


def main():
    try:
        payload = json.loads(sys.stdin.read())

        mode = payload.get("mode")
        expr_latex = payload.get("expr")
        var_name = payload.get("var")

        expr = parse_latex(expr_latex)

        if mode == "calc":
            result = simplify(expr)

        elif mode == "diff":
            var = symbols(var_name) if var_name else list(expr.free_symbols)[0]
            result = diff(expr, var)

        elif mode == "integrate":
            var = symbols(var_name) if var_name else list(expr.free_symbols)[0]
            result = integrate(expr, var)

        else:
            print("ERROR: unknown mode")
            return

        print(result)

    except Exception as e:
        # ★ Windows安全：ASCIIのみ
        print(f"ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
