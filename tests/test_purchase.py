"""bot.purchase() — the one function every buying button calls.

This is where your customer's money and Zentra's API meet, so it gets the
same three-outcome treatment ZentraShopBot's own orders.purchase() does:

  * Zentra delivers               → charge stands, order marked delivered
  * Zentra refuses CLEANLY        → refund, order marked failed
  * Zentra's call to ITS OWN      → NO refund (you may already have been
    supplier died in transit        charged), order marked unresolved

Getting the middle and the bottom cases backwards is the single most
expensive bug available in this codebase: swap them, and either a refused
order silently keeps a customer's money, or a genuinely charged order gets
refunded on top of the charge — and a customer who worked out how to trigger
the network failure gets your stock for free.

Run:  TEST_DATABASE_URL=postgresql://… python -m tests.test_purchase
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal

# bot.py builds its Config and its ZentraClient at import time, so these
# have to exist before the import — none of them are actually reached: the
# test swaps out bot.db, bot.zentra and bot.live before calling purchase().
os.environ.setdefault("BOT_TOKEN", "123456789:AAEtestTokenForOfflineTestsOnly12345678")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("ZENTRA_API_KEY", "zen_live_testtesttesttesttesttesttesttest01")

from tests.dbfixture import fresh_db, test_database_url  # noqa: E402

os.environ["DATABASE_URL"] = test_database_url()

from bot import bot as botmod  # noqa: E402
from bot.bot import PurchaseError, purchase  # noqa: E402
from bot.zentra_api import DeliveryField, Order, Product, ZentraError  # noqa: E402


class FakeZentra:
    """Stands in for zentra.ZentraClient. Three moods, picked per call."""

    def __init__(self):
        self.mode = "deliver"
        self.calls = 0
        self.seen_idempotency_keys: list[str] = []

    async def create_order(self, product_id, quantity, *, idempotency_key: str) -> Order:
        self.calls += 1
        self.seen_idempotency_keys.append(idempotency_key)

        if self.mode == "reject":
            raise ZentraError("Out of stock.", code="out_of_stock", status=409)
        if self.mode == "unresolved":
            raise ZentraError(
                "Could not confirm with our supplier.", code="upstream_timeout",
                status=202, unresolved=True,
            )
        return Order(
            id=str(1000 + self.calls), reference=f"ZEN-FAKE{self.calls:04d}",
            product_id=str(product_id), product_name="Fake Product", quantity=quantity,
            total=Decimal("1.00") * quantity, currency="USDT", status="delivered",
            created_at=None, completed_at=None,
            delivery=[DeliveryField("Code", f"secret-{self.calls}")],
        )


async def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    db = await fresh_db()
    fake = FakeZentra()

    # purchase() reads the module-level `db`, `zentra` and `live` names in
    # bot.py — swap them for the test doubles, the same technique
    # ZentraShopBot's own test suite uses to exercise a live module without
    # a real Telegram connection or a real Zentra account.
    #
    # `live` is rebuilt rather than reused: Settings(db) captures the actual
    # Db OBJECT at construction, so reassigning bot.db afterwards would not
    # retarget the original Settings instance at this fresh test database.
    from bot.settings import Settings
    botmod.db = db
    botmod.zentra = fake
    botmod.live = Settings(db)
    await botmod.live.refresh()

    product = Product(
        id="1", name="Test Widget", description="", unit_label="code",
        price=Decimal("1.00"), currency="USDT", stock=10, available=True,
    )

    async def customer(telegram_id: int, balance: str = "0") -> dict:
        row = await db.ensure_user(telegram_id, f"user{telegram_id}")
        if Decimal(balance) > 0:
            await db.adjust_balance(row["id"], Decimal(balance), "topup")
        return row

    # ---- the happy path -----------------------------------------------------

    print("\nA clean purchase")

    alice = await customer(8001, "10.00")
    result = await purchase(alice, product, 1)
    assert result["reference"].startswith("ZEN-FAKE")
    ok("Zentra delivers, and purchase() returns the reference and the delivery")

    balance = await db.balance(alice["id"])
    assert balance == Decimal("8.80"), balance  # 10.00 - (1.00 * 1.20 markup)
    ok("the customer is charged the MARKED-UP price, not Zentra's own price")

    order_row = await db.order_by_id(result["order_id"])
    assert order_row["status"] == "delivered"
    assert order_row["zentra_reference"] == result["reference"]
    ok("the local order record matches what was actually charged and delivered")

    # ---- insufficient balance -----------------------------------------------------

    print("\nNot enough money")

    bob = await customer(8002, "0.50")
    calls_before = fake.calls
    try:
        await purchase(bob, product, 1)
        raised = False
    except PurchaseError as exc:
        raised = True
        assert not exc.unresolved
        assert "Not enough balance" in str(exc)
    assert raised
    ok("a customer without enough balance is refused before Zentra is ever called")

    assert fake.calls == calls_before
    assert await db.balance(bob["id"]) == Decimal("0.50")
    ok("and Zentra was never asked, and the balance did not move")

    # ---- Zentra refuses cleanly: refund -----------------------------------------------------

    print("\nZentra refuses cleanly")

    carol = await customer(8003, "10.00")
    fake.mode = "reject"
    try:
        await purchase(carol, product, 1)
        raised = False
    except PurchaseError as exc:
        raised = True
        assert not exc.unresolved
    assert raised
    ok("a clean refusal from Zentra surfaces as a normal PurchaseError")

    assert await db.balance(carol["id"]) == Decimal("10.00")
    ok("and the customer's balance is back to exactly where it started — refunded")

    orders = await db.user_orders(carol["id"])
    assert orders[0]["status"] == "failed"
    ok("the order record says failed, not delivered and not still pending")

    # ---- Zentra is unsure: NO refund -----------------------------------------------------

    print("\nZentra is not sure — the case that must NOT be refunded")

    dave = await customer(8004, "10.00")
    fake.mode = "unresolved"
    try:
        await purchase(dave, product, 1)
        raised = False
    except PurchaseError as exc:
        raised = True
        assert exc.unresolved, "an unresolved Zentra failure must set unresolved=True"
    assert raised
    ok("an unresolved Zentra failure is a distinct kind of PurchaseError")

    assert await db.balance(dave["id"]) == Decimal("8.80"), (
        "the customer was refunded for an order that may have actually gone "
        "through — this is the bug that gives away free stock"
    )
    ok("the charge STANDS — refunding here would risk paying out twice")

    orders = await db.user_orders(dave["id"])
    assert orders[0]["status"] == "unresolved"
    ok("the order is marked unresolved, for a human to reconcile by hand")

    fake.mode = "deliver"

    # ---- idempotency -----------------------------------------------------

    print("\nThe idempotency key")

    erin = await customer(8005, "10.00")
    await purchase(erin, product, 2)
    assert fake.seen_idempotency_keys[-1].startswith("resell-")
    ok("every purchase sends its own freshly generated idempotency key to Zentra")

    keys = fake.seen_idempotency_keys
    assert len(keys) == len(set(keys)), "two purchases shared an idempotency key"
    ok("no two purchases in this run reused a key")

    await db.close()
    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
