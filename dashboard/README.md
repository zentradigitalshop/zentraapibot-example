# Admin Dashboard

A Next.js app, deployed on its own — same shape as
[ZentraShopBot's own admin dashboard](https://github.com/snackshell/zentrashopbot-admin):
server components reading the same PostgreSQL database the bot writes to, a
single-password login (no separate user system), and every write going
through a plain `<form action={...}>` server action rather than
client-side JavaScript.

See the root `README.md`'s "The admin dashboard" section for setup, and
`docs/GUIDE.md` §11 for the full design writeup.

## Pages

- **Overview** — customers, balances held, orders delivered, revenue, cost,
  profit, and deposits credited split by rail.
- **Orders** — searchable by username, Telegram id, order id, or Zentra
  reference; totals cards describe exactly what the search matched.
- **Customers** — searchable by username or Telegram id; delivered-order
  count and total spend per customer.
- **Deposits** — every top-up request on either rail, filterable by method,
  searchable by tx hash.
- **Credit by hand** — the fallback every payment rail keeps forever, from
  a form instead of raw SQL. Guarded by the same conditional-`UPDATE` rule
  as `bot/db.py`'s own `adjust_balance()`.
- **Settings** — the `settings` table's rows, one form per row, validated
  against the same rules `bot/settings.py` parses with.

## Testing

```bash
npm install
export TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/some_empty_db
npm test
```

`npm test` runs `node --test --experimental-strip-types` directly against
the `.ts` sources — no build step. A `.env.local` (see `.env.example`) is
only needed for `npm run dev` / `npm run build`, not for the tests: they
take the database URL from `TEST_DATABASE_URL` and never touch
`ADMIN_PASSWORD`-gated routes directly.
