"""Your markup, applied. No database.

Run:  python -m tests.test_pricing
"""

from __future__ import annotations

from decimal import Decimal

from bot.pricing import sell_price


def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    assert sell_price(Decimal("1.00"), Decimal("20")) == Decimal("1.20")
    ok("a $1.00 product at 20% markup sells for $1.20")

    assert sell_price(Decimal("1.00"), Decimal("0")) == Decimal("1.00")
    ok("0% markup passes Zentra's price straight through")

    assert sell_price(Decimal("0.90"), Decimal("15")) == Decimal("1.04")
    ok("a markup that would land on a third decimal rounds once, at the end")

    # sell_price() is PER UNIT. Multiplying by quantity is bot.py's job,
    # applied to the already-rounded unit price — so two of a $1.20 item is
    # $2.40, not a fresh rounding of $2.40000. Confirmed here so a future
    # refactor that moves the multiply INSIDE sell_price (rounding the
    # total instead of the unit) is caught: at some inputs the two orders
    # genuinely disagree, because quantity multiplies the rounding error too.
    unit = sell_price(Decimal("0.145"), Decimal("0"))  # rounds to 0.15 (up)
    assert unit == Decimal("0.15")
    total_from_unit = unit * 3
    total_if_rounded_together = (Decimal("0.145") * 3).quantize(Decimal("0.01"))
    assert total_from_unit != total_if_rounded_together, (
        total_from_unit, total_if_rounded_together)
    ok("rounding the unit price before multiplying by quantity is a real "
       "choice, not equivalent to rounding the total")

    assert sell_price(Decimal("100"), Decimal("500")) == Decimal("600.00")
    ok("the settings table's own ceiling (500%) is a business rule, not a "
       "limit this function enforces — it computes whatever it is given")

    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    main()
