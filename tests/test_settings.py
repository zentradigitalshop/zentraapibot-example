"""The settings overlay: bounds, types, and failing safe.

Run:  TEST_DATABASE_URL=postgresql://… python -m tests.test_settings
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from tests.dbfixture import fresh_db
from bot.settings import Settings


async def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    db = await fresh_db()
    live = Settings(db)

    assert live.get("markup_pct") is None
    ok("before the first refresh, nothing is loaded — never a guessed default")

    await live.refresh()
    assert live.decimal("markup_pct", "0") == Decimal("20")
    ok("after refresh, the shipped default (20%) is what a fresh install reads")

    await db.write_setting("markup_pct", "35", updated_by=None)
    await live.refresh()
    assert live.decimal("markup_pct", "0") == Decimal("35")
    ok("a change in the database is picked up on the next refresh")

    # The bound in the migration is 0 to 500. A value outside it is CLAMPED,
    # not rejected outright — the dashboard's own write path enforces the
    # bound before it ever reaches the table, so this is the second line of
    # defence for a row edited some other way (a manual SQL fix, a restore).
    await db.execute("UPDATE settings SET value = '9999' WHERE key = 'markup_pct'")
    await live.refresh()
    assert live.decimal("markup_pct", "0") == Decimal("500")
    ok("a value that slipped past the bound is clamped to the maximum on read")

    await db.execute("UPDATE settings SET value = 'not-a-number' WHERE key = 'markup_pct'")
    await live.refresh()
    assert live.decimal("markup_pct", "0") == Decimal("20")
    ok("a value that fails to parse falls back to that setting's own default")

    await db.close()
    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
