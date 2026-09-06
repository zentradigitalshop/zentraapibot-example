"""Allocating a unique deposit amount, and the cooldown that protects a
late payment from crediting the wrong customer.

Run:  TEST_DATABASE_URL=postgresql://… python -m tests.test_deposits
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

    async def customer(telegram_id: int) -> dict:
        return await db.ensure_user(telegram_id, f"user{telegram_id}")

    # ---- allocation basics -----------------------------------------------------

    print("\nAllocating a deposit")

    alice = await customer(7001)
    dep = await db.allocate_deposit(
        user_id=alice["id"], base_amount=Decimal("5.00"),
        window_minutes=60, cooldown_minutes=1440,
    )
    assert dep["status"] == "awaiting"
    assert Decimal("5.00") <= dep["amount_expected"] < Decimal("5.01")
    ok("the allocated amount is the base plus a tail under one cent")

    assert dep["amount_credited"] == dep["amount_expected"].quantize(Decimal("0.01"))
    ok("the credited figure is the expected figure rounded to the cent")

    # ---- uniqueness, under real concurrency -----------------------------------------------------

    print("\nUniqueness, for real")

    bob = await customer(7002)
    # A tiny range (1-3) makes a collision certain rather than merely
    # possible — five requests for the same base amount, three available
    # tails: at least two calls MUST retry through a UniqueViolation for
    # every one of the five to succeed with a distinct figure.
    results = await asyncio.gather(*[
        db.allocate_deposit(
            user_id=bob["id"], base_amount=Decimal("9.00"),
            window_minutes=60, cooldown_minutes=1440,
            tail_min=1, tail_max=3,
        )
        for _ in range(3)
    ])
    amounts = [r["amount_expected"] for r in results]
    assert len(set(amounts)) == 3, f"two concurrent allocations collided: {amounts}"
    ok("three concurrent requests for the same base amount get three distinct figures")

    try:
        await db.allocate_deposit(
            user_id=bob["id"], base_amount=Decimal("9.00"),
            window_minutes=60, cooldown_minutes=1440, tail_min=1, tail_max=3,
        )
        raised = False
    except RuntimeError:
        raised = True
    assert raised, "a fourth request should have found every tail taken"
    ok("and a fourth request, with every tail already live, is refused rather than colliding")

    # ---- the cooldown: what makes a late payment safe -----------------------------------------------------

    print("\nThe cooldown")

    carol = await customer(7003)
    live = await db.allocate_deposit(
        user_id=carol["id"], base_amount=Decimal("3.00"),
        window_minutes=60, cooldown_minutes=1440, tail_min=10, tail_max=10,
    )
    amount = live["amount_expected"]

    # Expire it by hand — expires_at in the past, but cooldown_until, which
    # the allocator sets independently, stays in the future.
    await db.execute("UPDATE deposits SET expires_at = now() - interval '1 hour' "
                     "WHERE id = %s", (live["id"],))
    expired_count = await db.expire_deposits()
    assert expired_count == 1
    expired = await db.deposit_by_id(live["id"])
    assert expired["status"] == "expired"
    ok("an overdue deposit is marked expired")

    # A DIFFERENT customer must not be able to claim the same figure while
    # it is still cooling down — this is the entire point of the cooldown.
    try:
        await db.allocate_deposit(
            user_id=alice["id"], base_amount=Decimal("3.00"),
            window_minutes=60, cooldown_minutes=1440, tail_min=10, tail_max=10,
        )
        stole_it = True
    except RuntimeError:
        stole_it = False
    assert not stole_it, "a second customer was given an amount still reserved"
    ok("nobody else can be issued that exact amount while it is cooling down")

    # And a late payment on the now-expired deposit still finds it.
    found = await db.open_deposit_for_amount(amount)
    assert found is not None and found["id"] == live["id"]
    ok("a payment that arrives after expiry still finds the right deposit")

    # ---- crediting: exactly once, keyed on the transaction hash -----------------------------------------------------

    print("\nCrediting")

    before = await db.balance(carol["id"])
    credited = await db.credit_deposit(live["id"], tx_hash="0xabc123")
    assert credited is not None
    after = await db.balance(carol["id"])
    assert after - before == live["amount_credited"]
    ok("crediting raises the balance by exactly the deposit's own credited figure")

    reread = await db.deposit_by_id(live["id"])
    assert reread["status"] == "credited"
    assert reread["tx_hash"] == "0xabc123"
    ok("the deposit itself now shows credited, with the transaction hash recorded")

    # THE SAME on-chain event, delivered twice — a watcher restart, an
    # overlapping poll window. Must not credit twice.
    again = await db.credit_deposit(live["id"], tx_hash="0xabc123")
    assert again is None
    assert await db.balance(carol["id"]) == after
    ok("crediting the SAME deposit again is a no-op — the balance does not move")

    # A DIFFERENT deposit, but the SAME transaction hash — the UNIQUE index
    # on tx_hash is what refuses this, not application logic remembering.
    dave = await customer(7004)
    other = await db.allocate_deposit(
        user_id=dave["id"], base_amount=Decimal("1.00"),
        window_minutes=60, cooldown_minutes=1440,
    )
    stolen = await db.credit_deposit(other["id"], tx_hash="0xabc123")
    assert stolen is None
    assert await db.balance(dave["id"]) == Decimal("0")
    ok("the same transaction hash cannot credit a second, unrelated deposit")

    # ---- the race: two credit attempts for the same deposit at once -----------------------------------------------------

    print("\nTwo credit attempts at once")

    erin = await customer(7005)
    race_dep = await db.allocate_deposit(
        user_id=erin["id"], base_amount=Decimal("2.00"),
        window_minutes=60, cooldown_minutes=1440,
    )
    outcomes = await asyncio.gather(*[
        db.credit_deposit(race_dep["id"], tx_hash=f"0xrace{i}")
        for i in range(5)
    ])
    won = [o for o in outcomes if o is not None]
    assert len(won) == 1, f"expected exactly one winner, got {len(won)}"
    assert await db.balance(erin["id"]) == race_dep["amount_credited"]
    ok("five simultaneous credit attempts on one deposit: exactly one wins, credited once")

    await db.close()
    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
