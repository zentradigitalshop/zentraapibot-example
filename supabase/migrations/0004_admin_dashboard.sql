-- Phase 4: the admin dashboard needs two things the bot itself never had to
-- store, because nothing before this read them back.

-- WHAT ZENTRA ACTUALLY CHARGED FOR THIS ORDER, so profit can be shown
-- honestly rather than promised and never computed — Phase 1's own comment
-- on this table said "nothing here computes it for you (that is Phase 4's
-- concern)"; this is that column.
--
-- This is NOT supplier-cost secrecy the way ZentraShopBot's own dashboard
-- treats cost_supplier — Zentra's price to YOUR key is not a hidden margin,
-- it is the literal amount your own wallet was debited, and you are
-- entitled to see it. Profit = price_snapshot - zentra_price_snapshot.
ALTER TABLE orders ADD COLUMN zentra_price_snapshot NUMERIC(18, 4);

COMMENT ON COLUMN orders.zentra_price_snapshot IS
    'What Zentra charged your own wallet for this order (product.price x '
    'quantity, at the moment it was placed) — independent of discount_usd '
    'or promotions. price_snapshot minus this is your profit on the order. '
    'NULL on an order that failed before Zentra was ever charged.';

-- A record of every manual balance change an admin makes from the
-- dashboard's Credit by hand page — who, how much, why, and when. Kept
-- apart from wallet_txns' own `ref` column (a free-text field meant for a
-- payment provider's own id) because a credit made by a person needs a
-- person's reason attached to it, searchable on its own terms.
CREATE TABLE admin_adjustments (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount_usd  NUMERIC(18, 4) NOT NULL,
    reason      TEXT NOT NULL,
    -- The wallet_txns row this adjustment produced — one adjustment, one
    -- ledger entry, always, so a balance and its own history can never
    -- silently disagree about a manual credit either.
    wallet_txn_id BIGINT NOT NULL REFERENCES wallet_txns(id) ON DELETE RESTRICT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT admin_adjustments_amount_not_zero CHECK (amount_usd <> 0),
    CONSTRAINT admin_adjustments_reason_given CHECK (length(trim(reason)) > 0)
);

CREATE INDEX idx_admin_adjustments_user ON admin_adjustments (user_id, created_at DESC);
