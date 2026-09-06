# The Zentra API Starter Guide

Everything you need to run your own resale shop on top of the
[Zentra Reseller API](https://zentradigital.shop/api/docs). This guide
grows with the project — each phase in the README adds its own section
here once it exists, rather than describing something you cannot run yet.

## 1. Get a Zentra API key

1. Open [@ZentraShopBot](https://t.me/ZentraShopBot) in Telegram.
2. Menu → **API Link** → **Create API key**.
3. **Copy the key immediately.** It is shown exactly once — Zentra stores
   only a fingerprint, and a key that scrolls off your screen unread is a
   key you must regenerate, which revokes the old one instantly.
4. Top up your Zentra wallet the same way you would to buy something in the
   shop yourself — Telebirr, Bank of Abyssinia, USDT, or Binance Pay. Every
   order this bot places is charged against this balance.

Your key looks like `zen_live_` followed by 40 characters. Put it in `.env`
as `ZENTRA_API_KEY` and never anywhere your own customers could see it —
not in a log line, not in an error message shown in chat.

## 2. Authentication — how this bot talks to Zentra

Every request carries your key as a bearer token:

```
Authorization: Bearer zen_live_YOUR_KEY
```

`bot/zentra_api.py` does this for you — you will not write an
`Authorization` header by hand anywhere in this codebase. If a request
comes back `401 unauthorized`, the key is wrong, revoked, or was never
sent; `403 account_suspended` means the Zentra account behind the key has
been banned, and no amount of retrying will fix either one.

## 3. Idempotency — why every order carries a key

`POST /v1/orders` accepts (and this starter always sends) an
`X-Idempotency-Key` header. Send the **same key** on a retry of the same
attempted purchase, and Zentra returns the **original** order instead of
placing a second one.

This matters here specifically because of what happens between your
customer's payment and Zentra's response:

```
customer pays you  →  your wallet debited  →  POST /v1/orders  →  ???
```

The `???` can be: delivered, cleanly refused, or **the network died before
you got an answer.** In that last case you do not know whether Zentra
placed the order. If your bot naively retried with a *new* idempotency
key, and the first attempt had actually succeeded, you would place — and
pay for — the same order twice.

`bot/bot.py`'s `purchase()` makes the key **once**, before Zentra is ever
called, and writes it to your own `orders` table **before** the request is
sent. If your process crashes and a retry mechanism resends the same
purchase, reusing that stored key is what makes the retry safe.

## 4. Money — the one rule that is not optional

**Every amount that touches a wallet, a price, or an order total is a
`Decimal`, parsed from a string.** Zentra sends every amount as a JSON
string (`"price": "0.90"`, never `"price": 0.90`) for exactly this reason:
a float cannot represent 0.90 exactly, and an error a customer can see in
their receipt is an error they will report.

If you extend this bot, the rule is simple: if a number is money, it comes
from `Decimal(str(...))`, and it gets rounded exactly once, at the moment
it is about to be shown or stored — `bot/money.py:cents()` is the only
place that rounds anything. Two partial roundings on the way to one answer
is how a ledger stops summing to itself.

## 5. The `unresolved` order — read this before you touch `purchase()`

`POST /v1/orders` can answer `HTTP 202` with `"code": "upstream_timeout"`
(or similar). This means: **Zentra's own call to its own supplier failed
in transit, and Zentra itself does not know if the order went through.**
Your wallet may already have been charged.

The one rule this demands: **do not refund your customer, and do not retry
with a new idempotency key.** Either action can produce a real loss — a
refund on top of a real charge gives your customer the product for free; a
fresh retry can place (and charge you for) a second, genuine duplicate.

`bot/bot.py`'s `purchase()` marks the order `unresolved` and leaves your
customer's charge exactly as it is, with a message explaining their order
is under review. As the reseller, you resolve it by checking
`GET /v1/orders/{id}` later — Zentra will have settled to a real status
(`delivered` or genuinely failed) by then — and crediting or delivering
by hand if needed.

## 6. Rate limits and the daily spend cap

Two independent limits apply to your key:

- **60 requests a minute** (`429 rate_limited`) — back off and retry. A
  `Retry-After` header, when present, tells you how long;
  `bot/zentra_api.py`'s `ZentraError.retry_after` surfaces it as a float.
- **A daily spend cap** (`429 daily_cap_reached`) — a ceiling on how much
  your key may spend in a rolling day, set by Zentra to limit exposure from
  a leaked key. Raising it is a conversation with Zentra, not a setting in
  this codebase.

Neither is retried automatically anywhere in this starter. A rate limit
hit during a real customer's purchase should surface to them as "try again
in a moment", not a silent infinite retry loop against Zentra's servers.

## 7. Payment rails — accepting money from *your* customers

This is a different question from anything above: Zentra only cares that
your *key* has a balance. How *your* customers pay *you* is entirely your
own system, covered rail by rail as each phase lands:

| Rail | Automatic detection | Status |
|---|---|---|
| Wallet credited by hand | — (an admin decision) | ✅ works today |
| USDT (BEP-20) | on-chain, watched by polling | ✅ works today |
| Binance Pay | read from the operator's own account, sweep-polled | ✅ works today |
| Telebirr | manual by default; automatic with LocalPaymentVerify | Phase 5 |
| Bank of Abyssinia | manual by default; automatic with LocalPaymentVerify | Phase 5 |

Every rail, once built, keeps the manual path as a permanent fallback —
never a payment that "didn't match" and simply vanished.

## 9. USDT (BEP-20) — how a payment is actually recognised

BEP-20 transfers carry no memo field. There is nowhere to write "this is
customer #42's top-up" — a transfer is just an amount moving from one
address to another. So **the amount itself is the fingerprint**: every
top-up request gets a tiny unique decimal tail added on top of what the
customer asked for (0.0001 to 0.0099 USDT), and that exact figure — tail
included — is what they are shown and what the watcher matches against.

```
customer asks for 5.00  →  allocated exactly 5.0042  →  told to send 5.0042
                                                              │
                                            watcher sees a Transfer of 5.0042
                                                              │
                                              matches the open deposit, credits it
```

**Why the tail matters more than it looks.** Without it, two customers
topping up 5.00 USDT at the same time would be indistinguishable on-chain —
whichever one paid first would be credited, and the second would have paid
into the void. `bot/db.py`'s `allocate_deposit()` is what makes the figure
unique: each candidate tail is attempted as an `INSERT`, and a partial
`UNIQUE` index on `(amount_expected) WHERE status = 'awaiting'` is what
actually decides whether it collided — not a `SELECT` taken a moment
earlier, which could already be stale by the time the `INSERT` lands.

**Why an expired request's amount stays reserved.** A customer's exchange
withdrawal can take longer than the request stayed open. If the exact
figure were released the instant it expired, a payment arriving five
minutes late could credit whoever is issued that same amount next —
`cooldown_until` is what stops that: the figure stays off-limits to new
requests well past the point the original one stopped being shown.

**Confirmations.** A payment is not trusted the moment it appears in a
block — a chain reorganisation can still remove it. `usdt_confirmations`
(default 3) is how many blocks must sit on top of it first. Lower is
faster; higher is safer against exactly that.

**Why this starter polls instead of subscribing.** ZentraShopBot's own bot
runs an event-driven WebSocket listener across a pool of RPC providers with
automatic failover — the right choice at real volume, where one provider's
outage must not mean a missed payment. This starter polls a single HTTP
endpoint every few seconds instead (`bot/chain/watcher.py`), because
requiring a reseller to configure provider failover before their first
payment can be accepted is exactly the kind of setup step that keeps a
starter kit from ever getting finished. The properties that actually
protect money — the confirmation delay, and crediting a deposit exactly
once per transaction hash — are unchanged either way.

**Turning it on** needs both sides to agree:

1. `.env`: `BSC_PAYMENT_ADDRESS` (your receiving address) and
   `BSC_HTTP_URL` (one RPC endpoint — dRPC, Ankr, PublicNode all have free
   tiers).
2. The dashboard setting `usdt_enabled = yes` (a button in Phase 4; direct
   SQL until then).

Either one missing, and the top-up screen is simply never offered — see
`bot/bot.py`'s `usdt_rail_live()`.

## 10. Binance Pay — not a merchant integration

Binance Pay's own merchant API needs a business account and an approval
process most resellers starting out have neither of. This rail instead
reads **your own personal Binance account's** Pay transaction history
through a signed, read-only API key. A customer sends an ordinary Binance
Pay transfer to your Binance ID (a UID); the bot asks Binance what your
own account received and matches it against open requests. No webhook,
no merchant approval — an ordinary account with an API key is enough.
This is exactly how ZentraShopBot itself does it.

**The same amount-as-fingerprint trick as USDT**, at a different default
precision (`binance_pay_tail_decimals`, default 4 — the same as USDT).
There is nothing else to key a match on: a Binance Pay transfer carries no
field that says "this is customer #42's top-up."

**One API call covers every open request.** Rather than asking Binance
once per pending deposit, `BinancePaySweeper` asks once for a window
covering the OLDEST open request and matches every transaction it gets
back against every deposit still waiting. A shop with nothing outstanding
makes no call at all. This matters because the Pay history endpoint is not
free to call, and Binance's own rate limits apply per account, not per
customer.

**Two independent signals must agree before a transaction counts as a
payment IN**: the sign of its amount, and whether its receiver id is the
operator's own account. Real Binance data has these always agree; the one
time they might not — a transaction type nobody anticipated, a schema
change — the transaction is refused rather than guessed at. This is worth
knowing if you ever see a "transaction is ambiguous" log line: that is the
correct, safe response to something the code does not recognise, not a
bug to silence.

**Exactly-once, no tolerance.** A payment for the wrong amount — even one
cent off — does not match, by explicit decision: there is no way to tell
an underpayment from a fee, and a tolerance is a discount anyone could
discover. Two transactions matching the same amount (the unique-amount
allocator makes this vanishingly rare, not impossible) is refused rather
than resolved by picking one — that would be choosing whose money it is
with no basis for the choice.

**Turning it on** needs both sides to agree, the same shape as USDT:

1. `.env`: `BINANCE_UID`, `BINANCE_API_KEY`, `BINANCE_API_SECRET` — a
   **read-only** key from Binance → Profile → API Management. Never grant
   it withdrawal or trading permission; this rail never needs to move
   money, only read history.
2. The dashboard setting `binance_pay_enabled = yes`.

## 11. The admin dashboard

A separate Next.js app, in `dashboard/`, deployed on its own — it reads and
writes the exact same PostgreSQL database as the bot, through the same
`settings`, `orders`, `deposits` and `users` tables, so the two never see a
different picture of your shop. Nothing about the bot changes because the
dashboard exists; nothing about the dashboard requires the bot to be
running, either — it is a second reader/writer of one database, not a
second brain.

**Why a separate app, not a page bolted onto the bot.** The bot is a
long-running process that holds a database connection open around the
clock and talks to Telegram; the dashboard is a handful of server-rendered
pages an admin opens a few times a day. Deploying them together would mean
every dashboard change risks the bot's uptime, and every bot dependency
(aiogram, the chain watcher, the Binance sweeper) ships to a surface that
never needs any of it.

### Authentication — one password, not a user system

`ADMIN_PASSWORD` is the whole of authentication. There is no accounts
table, no sign-up flow, no password reset — a solo reseller does not need
any of that machinery, and building it would be effort spent on a problem
this project does not have.

The session cookie is a signed token, not a database row: `issueSession()`
signs `{since, exp}` with an HMAC key **derived from `ADMIN_PASSWORD`
itself** (`dashboard/src/lib/session.ts`). That single fact is what makes
one shared password a defensible design rather than a shortcut — rotating
`ADMIN_PASSWORD` in your deployment's environment invalidates every
existing session, on every device, instantly, with no "sign out
everywhere" button to build or forget to click. `readSession()` verifies
the signature and the expiry in constant time (`timingSafeEqual`) and
fails closed on anything it cannot parse — a missing `ADMIN_PASSWORD`, a
tampered cookie, a session issued under an old password, all read back as
simply "not signed in," never a crash.

`session.ts` holds all of this with **no import of `next/headers`** —
deliberately, so `passwordMatches()` and `readSession()` can be exercised
by a plain `node --test`, the same as any other pure function in this
project. `dashboard/src/lib/auth.ts` is the thin, framework-coupled layer
on top: `currentAdmin()`/`requireAdmin()` read the actual cookie off the
real request, and every page starts with `await requireAdmin()` — a
middleware redirect to `/login` is a convenience for a signed-out visitor,
not the security boundary; each page checks for itself.

A crude per-process rate limiter (`tooManyAttempts`/`recordFailure`) slows
down guessing at the login form. It is honestly weaker on serverless than
it looks — a cold start is a fresh process with a fresh empty map — which
is exactly why the real defence documented here is a long, random
password, not this limiter.

### Connecting to the same database the bot uses

`dashboard/src/lib/db.ts` connects through the **session pooler** (port
5432), the same one `bot/db.py` uses — not the transaction pooler (6543)
usual serverless advice reaches for. Supavisor keeps a separate pool per
mode and tears one down once nothing is using it; the bot holds a
session-mode connection open around the clock, so that pool is always warm,
while a transaction-mode pool with only this dashboard using it gets torn
down between visits and cold-starts on the next one. That cold start is
what an occasional first-load timeout would look like, for no reason a
user could see. One administrator visiting occasionally, at `max: 1`
connections per invocation, is exactly the case session mode is fine for.

`withRetry()` (`dashboard/src/lib/retry.ts`) retries a **transient**
failure — a recycled pooler connection, a Supavisor cold start, a closed
socket — up to four times with increasing backoff, and never retries
anything else: a constraint violation retried three times just arrives
late, it does not become correct. Every page and server action that
touches the database wraps its query in this, for the same reason
`zentra_api.py`'s own client treats a `202 unresolved` as a real error
rather than a success it forgot to check.

### The same guard, every time money moves

`dashboard/src/lib/credit.ts`'s `creditByHand()` is "Credit by hand" from
the README — the fallback every payment rail keeps forever, for a payment
that arrives by some method the bot does not watch. It uses **the exact
same rule** as `bot/db.py`'s `adjust_balance()`: the non-negative check is
part of the `UPDATE`'s own `WHERE` clause, never a `SELECT` beforehand. An
admin debiting a customer at the same instant that customer spends the
last of their own balance from the bot is a real race — the dashboard and
the bot are two different processes hitting the same row — and only the
database serialising the two `UPDATE`s resolves it to exactly one winner.
`dashboard/tests/credit.test.mjs` proves this the same way every other
guard in this project is proved: ten simultaneous debits against a balance
that can only satisfy one, asserting exactly one wins, and a deliberate
sabotage of the guard during development that reproduced the database's
own `CHECK` constraint failing as a raw, unhandled error — confirming the
guard's necessity — before being restored.

### Settings, kept honest against the bot's own parsing

`dashboard/src/lib/settings.ts`'s `parse()` mirrors `bot/settings.py`'s
rules — the same `yes`/`no` spellings, the same integer-must-be-whole
check, the same min/max bounds read from the row itself rather than
hard-coded. The two are not generated from one source; what keeps them
from drifting is that both read their type and bounds from the same
`settings` row, so the only thing each side can get wrong is its own
interpretation of that row — which is exactly what
`dashboard/tests/settings.test.mjs` checks, independently of any database,
before ever testing the write path itself. `writeSetting()` writes the new
value and a `settings_history` row in one transaction, exactly like the
bot's own settings writes, and refuses outright — no value changed, no
history written — for an out-of-bounds value or a key that does not exist.

### The pages

Six of them, each a server component that runs its own `requireAdmin()`
check and reads through `withRetry`:

- **Overview** (`src/app/page.tsx`) — the shop-wide numbers, computed with
  SQL `SUM()` rather than pulled into JS and reduced, because this covers
  every row in the table, not a bounded search result.
- **Orders** and **Customers** — searchable (`searchOrders`/
  `searchCustomers` in `src/lib/`), with a `LIMIT 200` and their own
  display-only total cards computed by reducing over the *search result*,
  not the whole table — the distinction that matters is between a ledger
  figure (always SQL-side, always the full table) and a page's own summary
  of what it is currently showing.
- **Deposits** — every top-up request on either rail in one list,
  filterable by `method` so a $20 USDT request and a $20 Binance Pay
  request are never confused for one another here either.
- **Credit by hand** and **Settings** — described above.

Every write on every page is a plain `<form action={...}>` React Server
Action — no client-side fetch call assembling a request by hand, and the
app works with JavaScript disabled except for the two client components
(`CreditForm`, `SettingRowForm`) that use `useActionState` purely to show a
pending/result state without a full page reload.

### Testing it

`dashboard/tests/*.test.mjs` run with `node --test --experimental-strip-types`
directly against the `.ts` sources — Node's own TypeScript stripping, no
build step, no separate test framework. The one file that could not be
tested this way, `auth.ts`, imports `next/headers`, which only resolves
inside the Next.js runtime — which is exactly why the pure logic lives in
`session.ts` instead, and `auth.ts` is left as a thin wrapper nothing
tests directly. See the README's "The dashboard's own tests" for the exact
commands.

---

*This guide is versioned with the code. If a section describes behaviour
that does not match what is in `bot/`, the code is right and this file
needs an update — please open an issue.*
