"""Parsing a Binance Pay transaction, and matching it to a deposit.

No network, no database. Run:  python -m tests.test_binance_pay
"""

from __future__ import annotations

from decimal import Decimal

from bot.binance_pay import matching_transfer, parse

OWN_UID = "732609210"


def row(*, txid="P_A1", amount="5.0042", currency="USDT", when_ms=1_700_000_000_000,
       receiver=OWN_UID):
    r = {
        "transactionId": txid, "amount": amount, "currency": currency,
        "transactionTime": when_ms, "orderType": "C2C",
    }
    if receiver is not None:
        r["receiverInfo"] = {"binanceId": receiver}
    return r


def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    # ---- parsing -----------------------------------------------------

    print("\nParsing")

    t = parse(row(), OWN_UID)
    assert t is not None
    assert t.txid == "P_A1"
    assert t.amount == Decimal("5.0042")
    assert t.incoming is True
    ok("an ordinary incoming payment parses with every field correct")

    # An OUTGOING payment: negative amount, receiver is the OTHER party.
    outgoing = parse(row(amount="-2.00", receiver="999999"), OWN_UID)
    assert outgoing is not None
    assert outgoing.incoming is False
    assert outgoing.amount == Decimal("2.00")  # stored as a magnitude
    ok("an outgoing payment (this account paying someone else) parses too — as outgoing")

    assert parse({"amount": "1.00"}, OWN_UID) is None
    ok("a row missing transactionId is unusable")

    assert parse(row(amount="not-a-number"), OWN_UID) is None
    ok("a row with an unparseable amount is unusable")

    assert parse(row(when_ms=0), OWN_UID) is None
    assert parse(row(when_ms=-5), OWN_UID) is None
    ok("a row with no sane timestamp is unusable")

    assert parse("not even a dict", OWN_UID) is None
    ok("garbage input does not raise")

    # THE TWO-SIGNAL CHECK: amount says incoming (positive), but the
    # receiver is NOT this account. Real data always agrees; if it ever
    # doesn't, the safe answer is "not usable", not a guess.
    ambiguous = parse(row(amount="5.00", receiver="someone-else"), OWN_UID)
    assert ambiguous is None
    ok("a positive amount whose receiver is NOT this account is refused, not guessed at")

    no_receiver = parse(row(receiver=None), OWN_UID)
    assert no_receiver is None
    ok("a positive amount with no receiver info at all is refused the same way")

    # ---- matching -----------------------------------------------------

    print("\nMatching one transaction to one deposit")

    window = dict(opened_ms=1_699_999_000_000, closed_ms=1_700_001_000_000)

    rows = [row(amount="5.0042")]
    m = matching_transfer(rows, expected=Decimal("5.0042"), own_uid=OWN_UID, **window)
    assert m is not None and m.txid == "P_A1"
    ok("an exact-amount incoming USDT payment inside the window matches")

    assert matching_transfer(rows, expected=Decimal("5.0043"), own_uid=OWN_UID, **window) is None
    ok("one cent of difference — or one ten-thousandth — does not match; no tolerance")

    assert matching_transfer(
        [row(amount="5.0042", currency="BUSD")], expected=Decimal("5.0042"),
        own_uid=OWN_UID, **window,
    ) is None
    ok("the right amount in the wrong asset does not match")

    assert matching_transfer(
        [row(amount="-5.0042", receiver="someone-else")], expected=Decimal("5.0042"),
        own_uid=OWN_UID, **window,
    ) is None
    ok("an outgoing transaction for the same figure does not match — it is not a payment IN")

    outside = [row(amount="5.0042", when_ms=1_699_000_000_000)]  # before the window
    assert matching_transfer(outside, expected=Decimal("5.0042"), own_uid=OWN_UID, **window) is None
    ok("a payment made before the deposit existed does not match, however exact the amount")

    # TWO transactions at the exact same amount — the unique-amount
    # allocator makes this rare, but "rare" is not "impossible", and this
    # must refuse to guess rather than pick one.
    two = [row(txid="P_A1", amount="5.0042"), row(txid="P_A2", amount="5.0042")]
    assert matching_transfer(two, expected=Decimal("5.0042"), own_uid=OWN_UID, **window) is None
    ok("two transactions matching the same amount: refused, not arbitrarily resolved")

    assert matching_transfer([], expected=Decimal("5.0042"), own_uid=OWN_UID, **window) is None
    ok("no transactions at all is simply no match, not an error")

    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    main()
