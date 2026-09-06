-- Phase 1: your own customers, their wallets, and the orders you place on
-- their behalf against your Zentra API key.
--
-- THIS SCHEMA IS FOR YOUR CUSTOMERS, NOT FOR ZENTRA'S. You resell through
-- your own bot; your customers top up a balance here, in YOUR database, and
-- spending it places an order through the Zentra API using YOUR key. Zentra
-- has no idea these rows exist — as far as Zentra is concerned, one
-- customer (you) is placing every order.
--
-- Money habits, carried from ZentraShopBot because they were paid for there
-- and there is no reason to relearn them:
--
--   * every amount is NUMERIC, never FLOAT — a float cannot hold 0.10
--     exactly, and a rounding error a customer can see is one they report.
--   * a balance changes ONLY through wallet_txns, in the same transaction as
--     the ledger row, so the balance and its own history can never disagree.
--   * a debit is guarded in the UPDATE's own WHERE clause, not by a SELECT
--     before it — see bot/db.py adjust_balance() for why: two purchases
--     racing on the last of a balance must not both pass the same check.

CREATE TABLE users (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    telegram_id   BIGINT NOT NULL UNIQUE,
    username      TEXT,
    balance_usd   NUMERIC(18, 4) NOT NULL DEFAULT 0,
    banned        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT users_balance_sane CHECK (balance_usd >= 0)
);

CREATE INDEX idx_users_telegram_id ON users (telegram_id);

-- Every change to a balance, in order, forever. This is the ONLY table a
-- balance is ever computed from if the users.balance_usd column is ever in
-- doubt — it is a cache of this sum, kept exact by adjust_balance()'s guard,
-- never the other way around.
CREATE TABLE wallet_txns (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount_usd  NUMERIC(18, 4) NOT NULL,
    -- 'topup', 'purchase', 'refund', 'admin_credit', 'admin_debit' — open,
    -- like ZentraShopBot's own column, rather than an enum: a payment rail
    -- added in a later migration introduces its own kind without touching
    -- this table's shape.
    kind        TEXT NOT NULL,
    -- The idempotency key of the purchase that caused this, or a payment
    -- provider's own transaction id — whatever makes this row traceable to
    -- the event that produced it. Not unique: a refund and its original
    -- purchase legitimately share no reference, but a topup replayed by a
    -- provider's webhook must not double-credit, which is why callers that
    -- CAN supply one, do.
    ref         TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_wallet_txns_user ON wallet_txns (user_id, created_at DESC);

-- One order placed against Zentra, on behalf of one of YOUR customers.
--
-- zentra_order_id and zentra_reference are what Zentra calls this order —
-- keep both: the id is a database key you would use to look it up again
-- through GET /v1/orders/{id}, the reference (ZEN-XXXXXXXX) is what you would
-- read back to a customer or quote to Zentra's own support.
--
-- price_snapshot is what YOUR customer paid you, in YOUR currency and at
-- YOUR markup — completely independent of what Zentra charged your wallet
-- for the same order. The two are expected to differ; that difference is
-- your margin, and nothing here computes it for you (that is Phase 4's
-- settings + markup rule, not a fact this table needs to store to be
-- correct today).
CREATE TABLE orders (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id             BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    zentra_product_id   TEXT NOT NULL,
    product_name        TEXT NOT NULL,
    quantity            INTEGER NOT NULL,
    price_snapshot      NUMERIC(18, 4) NOT NULL,
    -- pending: charged your customer, has not yet reached Zentra.
    -- delivered: Zentra confirmed and handed back the goods.
    -- unresolved: Zentra's own call to ITS supplier died in transit — your
    --   Zentra wallet may have been charged; do not retry automatically.
    -- refunded: your customer was given their money back.
    status              TEXT NOT NULL DEFAULT 'pending',
    zentra_order_id     TEXT,
    zentra_reference    TEXT,
    -- The idempotency key sent to POST /v1/orders. Kept so a retry after a
    -- timeout reuses the SAME key rather than minting a new one — see
    -- zentra_api.py's create_order() for why that is the whole point of it.
    idempotency_key     TEXT NOT NULL UNIQUE,
    delivered_payload    JSONB,
    error               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ,

    CONSTRAINT orders_status_known CHECK (
        status IN ('pending', 'delivered', 'unresolved', 'refunded', 'failed')
    ),
    CONSTRAINT orders_quantity_positive CHECK (quantity > 0)
);

CREATE INDEX idx_orders_user ON orders (user_id, created_at DESC);
CREATE INDEX idx_orders_status ON orders (status) WHERE status IN ('pending', 'unresolved');

-- Business settings, editable from the admin dashboard without a restart —
-- the same self-describing shape ZentraShopBot's settings table uses. Each
-- row carries its own type and bounds, so the dashboard and the bot enforce
-- the same rule rather than two copies that drift.
CREATE TABLE settings (
    key             TEXT PRIMARY KEY,
    value           TEXT,
    default_value   TEXT NOT NULL,
    value_type      TEXT NOT NULL,
    min_value       NUMERIC,
    max_value       NUMERIC,
    category        TEXT NOT NULL,
    label           TEXT NOT NULL,
    help            TEXT NOT NULL,
    sort_order      INTEGER NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ,
    updated_by      BIGINT REFERENCES users(id) ON DELETE SET NULL,

    CONSTRAINT settings_value_type_known CHECK (
        value_type IN ('bool', 'int', 'decimal', 'text')
    )
);

-- A record of every change, so "who set the markup to 200% at 2am" has an
-- answer. Written in the same transaction as the setting itself changing.
CREATE TABLE settings_history (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    key         TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    changed_by  BIGINT REFERENCES users(id) ON DELETE SET NULL,
    changed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_settings_history_key ON settings_history (key, changed_at DESC);

INSERT INTO settings
    (key, default_value, value_type, min_value, max_value, category, label, help, sort_order)
VALUES
('markup_pct', '20', 'decimal', 0, 500, 'pricing',
 'Your markup over Zentra''s price (%)',
 'What your customers pay is Zentra''s price plus this percentage. 20 means '
 'a $1.00 Zentra product sells for $1.20 in your shop.', 100),

('min_topup_usd', '1', 'decimal', 0, 100000, 'wallet',
 'Smallest top-up accepted (USDT)',
 'A top-up below this is refused before any payment rail is asked to '
 'confirm it.', 200);
