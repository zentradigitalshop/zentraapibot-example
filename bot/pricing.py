"""What YOUR customer pays, derived from what Zentra charges you.

One rule, one place — the dashboard's markup setting is the only knob, and
every screen that shows a price computes it here rather than storing a price
that could go stale the moment Zentra's own price moves.
"""

from __future__ import annotations

from decimal import Decimal

from .money import cents


def sell_price(zentra_price: Decimal, markup_pct: Decimal) -> Decimal:
    """Zentra's price plus your markup, rounded once, at the end.

    Rounding the markup and the price separately before adding them is how
    a shop ends up a cent off from its own displayed total — so the whole
    expression is computed in exact Decimal arithmetic first, and cents()
    is called exactly once, on the final figure.
    """
    return cents(zentra_price * (Decimal(1) + markup_pct / Decimal(100)))
