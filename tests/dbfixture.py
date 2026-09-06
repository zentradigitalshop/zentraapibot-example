"""A real PostgreSQL database for the test suite.

Same reasoning as ZentraShopBot's own tests/dbfixture.py: payment safety here
rests on things only a real database enforces — the CHECK constraint that
refuses a negative balance, the UNIQUE constraint on idempotency_key deciding
a race — and a mock would happily agree with whatever the code did.

Point TEST_DATABASE_URL at any empty PostgreSQL. It is NEVER production:
setup drops every table in the public schema first, so a misdirected run
would destroy real data. A URL pointing at supabase.com is refused outright.
"""

from __future__ import annotations

import os
import pathlib

from bot.db import Db

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))

DEFAULT_URL = "postgresql://zentra@127.0.0.1:5432/zentraapi_test"


def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "").strip() or DEFAULT_URL
    if "supabase.co" in url or "supabase.com" in url:
        raise RuntimeError(
            "TEST_DATABASE_URL points at a Supabase project. The fixture "
            "drops every table in the public schema — refusing to run "
            "against it."
        )
    return url


async def fresh_db() -> Db:
    """An empty database with this project's migrations applied."""
    url = test_database_url()
    # max_size is generous on purpose: the race tests fire many
    # concurrent operations at once, and a pool too small to grant them
    # all a connection SIMULTANEOUSLY makes the pool itself serialise
    # the race away — which would make a broken guard pass by accident.
    db = Db(url, min_size=1, max_size=20)

    await db._pool.open(wait=True, timeout=30)

    async with db._pool.connection() as conn:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        for path in MIGRATIONS:
            await conn.execute(path.read_text())
    return db
