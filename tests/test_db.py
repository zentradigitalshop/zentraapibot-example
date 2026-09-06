"""The database layer: balances, the wallet ledger, and orders.

Every guard here exists because a race WILL happen the first busy day this
bot has — a customer double-tapping Buy, two admin credits landing at once.
Each one is verified below by firing concurrent operations at the real
database and checking exactly one of them won, not by trusting the SQL.

Run:  TEST_DATABASE_URL=postgresql://… python -m tests.test_db
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from tests.dbfixture import fresh_db


async def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    db = await fresh_db()

    # ---- customers -----------------------------------------------------

    print("\nCustomers")

    alice = await db.ensure_user(9001, "alice")
    again = await db.ensure_user(9001, "alice_renamed")
    assert alice["id"] == again["id"]
    assert again["username"] == "alice_renamed"
    ok("the same telegram id returns the same row, with the username kept current")

    assert await db.user_by_telegram_id(9001) is not None
    assert await db.user_by_telegram_id(999999) is None
    ok("a customer who never started the bot does not exist")

    # ---- the balance guard -----------------------------------------------------

    print("\nThe balance guard")

    bob = await db.ensure_user(9002, "bob")
    assert await db.adjust_balance(bob["id"], Decimal("10.00"), "topup") is True
    assert await db.balance(bob["id"]) == Decimal("10.00")
    ok("a credit raises the balance and is journaled")

    assert await db.adjust_balance(bob["id"], Decimal("-3.00"), "purchase") is True
    assert await db.balance(bob["id"]) == Decimal("7.00")
    ok("a debit that fits is allowed")

    assert await db.adjust_balance(bob["id"], Decimal("-100.00"), "purchase") is False
    assert await db.balance(bob["id"]) == Decimal("7.00")
    ok("a debit that would overdraw is refused, and changes nothing")

    history = await db.wallet_history(bob["id"])
    assert len(history) == 2, "the refused debit wrote a row anyway"
    ok("the refused debit left no ledger entry — only the two that succeeded")

    # THE RACE. Ten purchases at once against a balance of exactly one of
    # them. Exactly one may succeed; a check-then-write pair of statements
    # cannot guarantee this, and that is the whole reason the guard lives in
    # the UPDATE's own WHERE clause.
    carol = await db.ensure_user(9003, "carol")
    await db.adjust_balance(carol["id"], Decimal("5.00"), "topup")
    results = await asyncio.gather(*[
        db.adjust_balance(carol["id"], Decimal("-5.00"), "purchase")
        for _ in range(10)
    ])
    assert sum(results) == 1, f"expected exactly one winner, got {sum(results)}"
    assert await db.balance(carol["id"]) == Decimal("0.00")
    ok("ten simultaneous debits against one balance's worth: exactly one wins")

    # ---- orders -----------------------------------------------------------

    print("\nOrders")

    dave = await db.ensure_user(9004, "dave")
    order = await db.create_pending_order(
        user_id=dave["id"], zentra_product_id="1", product_name="Test Product",
        quantity=1, price_snapshot=Decimal("1.20"), idempotency_key="idem-dave-1",
    )
    assert order["status"] == "pending"
    ok("a pending order exists before Zentra is ever called")

    await db.mark_order_delivered(
        order["id"], zentra_order_id="99", zentra_reference="ZEN-ABCD1234",
        delivered_payload='[{"label":"Code","value":"secret"}]',
    )
    delivered = await db.order_by_id(order["id"])
    assert delivered["status"] == "delivered"
    assert delivered["zentra_reference"] == "ZEN-ABCD1234"
    ok("marking delivered records Zentra's own reference and the payload")

    # THE UNIQUE CONSTRAINT. Two orders cannot share an idempotency key —
    # this is what makes a retry after a crash safe to attempt rather than
    # a second charge waiting to happen.
    try:
        await db.create_pending_order(
            user_id=dave["id"], zentra_product_id="2", product_name="Other",
            quantity=1, price_snapshot=Decimal("1.00"), idempotency_key="idem-dave-1",
        )
        raised = False
    except Exception:  # noqa: BLE001 — psycopg's own UniqueViolation
        raised = True
    assert raised, "a duplicate idempotency key was accepted"
    ok("a duplicate idempotency key is refused by the database itself")

    rows = await db.user_orders(dave["id"])
    assert len(rows) == 1
    ok("and the customer still has exactly one order to show for it")

    # ---- settings -----------------------------------------------------------

    print("\nSettings")

    row = await db.setting_row("markup_pct")
    assert row is not None and row["default_value"] == "20"
    ok("the shipped default markup is 20%, as the migration says")

    await db.write_setting("markup_pct", "35", updated_by=None)
    changed = await db.setting_row("markup_pct")
    assert changed["value"] == "35"
    ok("writing a setting changes what is read back")

    history_rows = await db.fetchall(
        "SELECT * FROM settings_history WHERE key = 'markup_pct' ORDER BY changed_at")
    assert len(history_rows) == 1
    assert history_rows[0]["old_value"] is None  # was never set before
    assert history_rows[0]["new_value"] == "35"
    ok("the change is journaled with what it was before and what it became")

    await db.close()
    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
