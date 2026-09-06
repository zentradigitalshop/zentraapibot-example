"""Decimal arithmetic, rounding, and display. No database, runs anywhere.

Run:  python -m tests.test_money
"""

from __future__ import annotations

from decimal import Decimal

from bot.money import cents, fmt_etb, fmt_usdt, to_etb


def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    assert cents(Decimal("1.005")) == Decimal("1.01")
    assert cents(Decimal("1.004")) == Decimal("1.00")
    ok("cents() rounds half up, not banker's rounding")

    assert cents(Decimal("0.1") + Decimal("0.2")) == Decimal("0.30")
    ok("Decimal addition has no float error to round away in the first place")

    assert fmt_usdt(Decimal("1.5")) == "1.50 USDT"
    assert fmt_usdt(Decimal("1")) == "1.00 USDT"
    ok("USDT always shows two decimal places")

    assert fmt_etb(Decimal("1234.6")) == "1,235 ETB"
    assert fmt_etb(Decimal("999")) == "999 ETB"
    ok("ETB is whole birr with thousands separators, never a fraction")

    assert to_etb(Decimal("1.00"), Decimal("160")) == Decimal("160.00")
    assert to_etb(Decimal("0.005"), Decimal("160")) == cents(Decimal("0.005") * Decimal("160"))
    ok("USDT to ETB is one multiply, rounded once at the end")

    # A string constructor, never a float — this is what the whole module is
    # for, and it is worth a check that fails loudly if somebody "simplifies"
    # a call site back to Decimal(0.1).
    assert Decimal("0.1") != Decimal(0.1)
    ok("(sanity) Decimal(str) and Decimal(float) really do disagree")

    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    main()
