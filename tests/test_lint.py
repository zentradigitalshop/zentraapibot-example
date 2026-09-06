"""Static checks over the whole codebase — the ones a unit test cannot catch
because no test happens to walk the broken line.

Run:  python -m tests.test_lint
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FATAL = ("undefined name", "redefinition of unused", "f-string is missing placeholders")


def test_no_undefined_names() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pyflakes", "bot", "tests"],
        cwd=ROOT, capture_output=True, text=True,
    )
    problems = [
        line for line in result.stdout.splitlines()
        if any(marker in line for marker in FATAL)
    ]
    assert not problems, (
        "static analysis found names that do not exist at runtime:\n  "
        + "\n  ".join(problems)
    )
    print(f"  no undefined names in bot/ or tests/ ({len(result.stdout.splitlines())} advisory notes)")


def test_no_float_in_money_paths() -> None:
    """money.py, pricing.py, db.py, bot.py and zentra_api.py must never
    construct Decimal from a float literal — Decimal(0.1) captures the
    float's own rounding error before Decimal ever sees it, and it is the
    one mistake that is easy to make without any test noticing, because 0.1
    LOOKS fine until it is summed a few hundred times.

    Walks the real AST rather than grepping text, so this only ever inspects
    actual code — a docstring that happens to contain the string
    "Decimal(0.1)" while explaining this very rule is not a violation.
    """
    import ast

    offenders = []
    money_files = ["bot/money.py", "bot/pricing.py", "bot/db.py", "bot/bot.py",
                   "bot/zentra_api.py"]
    for relpath in money_files:
        path = ROOT / relpath
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(), filename=relpath)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "Decimal"):
                continue
            if len(node.args) != 1:
                continue
            arg = node.args[0]
            is_float_literal = isinstance(arg, ast.Constant) and isinstance(arg.value, float)
            # Decimal(-0.1) parses as UnaryOp(USub, Constant(0.1)), not a
            # single negative Constant — catch that shape too.
            is_negated_float = (
                isinstance(arg, ast.UnaryOp) and isinstance(arg.op, ast.USub)
                and isinstance(arg.operand, ast.Constant)
                and isinstance(arg.operand.value, float)
            )
            if is_float_literal or is_negated_float:
                offenders.append(f"{relpath}:{node.lineno}")
    assert not offenders, (
        "Decimal(<float literal>) found — construct from a string instead:\n  "
        + "\n  ".join(offenders)
    )
    print("  no money file constructs Decimal from a float literal")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            print(name)
            fn()
    print("\nstatic checks passed")
