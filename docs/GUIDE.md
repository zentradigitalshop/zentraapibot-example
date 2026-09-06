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
| USDT (BEP-20) | on-chain, watched live | Phase 2 |
| Binance Pay | Binance's merchant webhook | Phase 3 |
| Telebirr | manual by default; automatic with LocalPaymentVerify | Phase 5 |
| Bank of Abyssinia | manual by default; automatic with LocalPaymentVerify | Phase 5 |

Every rail, once built, keeps the manual path as a permanent fallback —
never a payment that "didn't match" and simply vanished.

## 8. The admin dashboard

Coming in Phase 4: settings (your markup, which rails are live), a live
order feed, your customer list, and Credit by hand — the fallback every
rail above depends on. Until then, the same things are done directly
against the database; see the README's Quickstart for the exact SQL.

---

*This guide is versioned with the code. If a section describes behaviour
that does not match what is in `bot/`, the code is right and this file
needs an update — please open an issue.*
