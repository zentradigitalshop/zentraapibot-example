-- Phase 2: USDT (BEP-20), watched automatically on-chain.
--
-- HOW A PAYMENT IS RECOGNISED. BEP-20 transfers carry no memo field — there
-- is nothing to write "customer #42" into. So the AMOUNT is the fingerprint:
-- every deposit request gets a tiny unique decimal tail added to it
-- (0.0001 to 0.0099 USDT), and that exact figure — tail included, because
-- the tail is the customer's money, not a surcharge — is what they are
-- told to send and what the chain watcher matches against.
--
-- THE AMOUNT MUST BE UNIQUE AMONG LIVE REQUESTS, and the database decides
-- that, not a SELECT beforehand — see bot/db.py's allocate_deposit() for
-- why: two requests choosing a tail at the same instant must not collide.
--
-- THE AMOUNT STAYS RESERVED AFTER A REQUEST EXPIRES. A customer's exchange
-- withdrawal can take longer than the request stayed open, and if the exact
-- amount were freed the moment it expired, that late payment could be
-- credited to whoever was issued the same figure next. cooldown_until
-- outlives the request itself for exactly this reason.
--
-- A TRANSACTION HASH CREDITS AT MOST ONE DEPOSIT, EVER — the UNIQUE
-- constraint below, not application logic, is what makes a duplicate
-- delivery of the same on-chain event a no-op rather than a double credit.

CREATE TABLE deposits (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id             BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- The exact figure the customer was told to send, tail included. This
    -- is the ONLY thing that identifies which deposit an on-chain transfer
    -- belongs to — see the comment above.
    amount_expected     NUMERIC(24, 8) NOT NULL,
    -- What actually gets credited on payment. Equal to amount_expected
    -- today (the whole figure, tail included, is real money) — kept as its
    -- own column because a future rail might round the credited amount
    -- differently from what it asks the customer to send.
    amount_credited     NUMERIC(18, 4) NOT NULL,

    status              TEXT NOT NULL DEFAULT 'awaiting',
    tx_hash             TEXT,
    credited_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at          TIMESTAMPTZ NOT NULL,
    -- Outlives expires_at — see the header comment on why.
    cooldown_until      TIMESTAMPTZ NOT NULL,

    CONSTRAINT deposits_status_known CHECK (
        status IN ('awaiting', 'expired', 'credited')
    ),
    CONSTRAINT deposits_amount_positive CHECK (amount_expected > 0)
);

-- The uniqueness that makes amount-only matching safe: at most one AWAITING
-- deposit may expect a given amount, decided by the database at the moment
-- of writing rather than by a SELECT that only describes the past.
--
-- This alone does not close the cooldown gap — an EXPIRED deposit's amount
-- is not covered by "status = 'awaiting'". The allocator in bot/db.py closes
-- that half with an INSERT ... WHERE NOT EXISTS (checking cooldown_until),
-- and idx_deposits_cooldown_lookup below is only there to make that lookup
-- fast, not to enforce anything itself — a partial index predicate cannot
-- reference now() at all (it must be IMMUTABLE), which is why the actual
-- guarantee has to live in a column comparison the allocator makes, not in
-- the index's own WHERE clause.
CREATE UNIQUE INDEX idx_deposits_amount_awaiting
    ON deposits (amount_expected)
    WHERE status = 'awaiting';

CREATE INDEX idx_deposits_cooldown_lookup ON deposits (amount_expected, cooldown_until);

-- A transaction credits AT MOST ONE deposit. NULL (not yet paid) does not
-- collide with itself — a standard property of a UNIQUE index over a
-- nullable column — so many awaiting deposits can share tx_hash = NULL.
CREATE UNIQUE INDEX idx_deposits_tx_hash ON deposits (tx_hash) WHERE tx_hash IS NOT NULL;

CREATE INDEX idx_deposits_user ON deposits (user_id, created_at DESC);
CREATE INDEX idx_deposits_awaiting ON deposits (status) WHERE status = 'awaiting';

-- Internal bookkeeping — NOT a business setting, and deliberately not in
-- the `settings` table an admin edits: the chain watcher's own memory of
-- how far it has looked, so a restart resumes from there instead of
-- re-scanning from the token contract's creation block.
CREATE TABLE bot_state (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

INSERT INTO settings
    (key, default_value, value_type, min_value, max_value, category, label, help, sort_order)
VALUES
('usdt_enabled', 'no', 'bool', NULL, NULL, 'payments',
 'USDT (BEP-20) top-ups',
 'Watches your receiving address on-chain and credits a customer''s wallet '
 'automatically once a payment is confirmed. Needs BSC_HTTP_URL and '
 'BSC_PAYMENT_ADDRESS set in .env — this switch does not start the watcher '
 'on its own if those are missing.', 300),

('deposit_window_minutes', '60', 'int', 5, 1440, 'payments',
 'Payment request expiry (minutes)',
 'How long a USDT request stays open before it is marked expired. A late '
 'payment can still be credited afterwards — see the cooldown below.', 310),

('usdt_amount_cooldown_minutes', '1440', 'int', 60, 20160, 'payments',
 'Amount reservation after expiry (minutes)',
 'How long an expired request''s exact amount stays out of circulation. '
 'This is what stops a payment delayed past expiry from crediting whoever '
 'is issued that same amount next — set it comfortably above the slowest '
 'exchange withdrawal you expect a customer to make.', 320),

('usdt_confirmations', '3', 'int', 1, 30, 'payments',
 'Confirmations required before crediting',
 'How many blocks must sit on top of a payment before it is trusted. Lower '
 'is faster; higher is safer against a chain reorganisation reversing a '
 'transfer that looked confirmed a moment before.', 330);
