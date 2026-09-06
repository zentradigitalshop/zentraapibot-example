-- Phase 3: Binance Pay, read from YOUR OWN account's payment history.
--
-- THERE IS NO MERCHANT ACCOUNT HERE, ON PURPOSE — matching how
-- ZentraShopBot itself does this. A customer sends an ordinary Binance Pay
-- transfer to your personal Binance ID (a UID, not a merchant integration),
-- and the bot confirms it by reading YOUR OWN account's Pay history through
-- a signed, read-only request. No webhook, no merchant approval process —
-- an ordinary Binance account with an API key is enough.
--
-- THE SAME AMOUNT-AS-FINGERPRINT TRICK AS USDT, for the same reason: a
-- Binance Pay transfer carries no field a bot could read "customer #42"
-- out of, so the exact figure — a unique decimal tail added on top — is
-- what identifies which request a payment belongs to.
--
-- deposits GAINS A METHOD, rather than a second table, because every rule
-- already written for USDT — unique while awaiting, reserved through a
-- cooldown, credited at most once per external reference — applies here
-- unchanged. Only the discriminator (method) and the matching worker
-- (which reads Binance's API instead of a chain) differ.

ALTER TABLE deposits ADD COLUMN method TEXT NOT NULL DEFAULT 'usdt';
ALTER TABLE deposits ADD CONSTRAINT deposits_method_known
    CHECK (method IN ('usdt', 'binancepay'));

COMMENT ON COLUMN deposits.tx_hash IS
    'The replay key from whichever payment method credited this deposit — '
    'a 0x-prefixed chain transaction hash for method=usdt, or a Binance '
    'transactionId (a "P_..." string) for method=binancepay. Column name '
    'kept from Phase 2 rather than churned for a rename; what matters is '
    'that it UNIQUELY identifies the external event, whatever shape that '
    'takes.';

-- The two indexes from migration 0002, RESCOPED BY METHOD. Two rails are
-- matched by two completely different workers reading two completely
-- different systems — a $20 USDT request and a $20 Binance Pay request
-- can never be confused for one another, so there is no reason to make
-- them compete for the same amount-space. Each rail gets its own full
-- range of tails.
DROP INDEX idx_deposits_amount_awaiting;
CREATE UNIQUE INDEX idx_deposits_amount_awaiting
    ON deposits (method, amount_expected)
    WHERE status = 'awaiting';

DROP INDEX idx_deposits_cooldown_lookup;
CREATE INDEX idx_deposits_cooldown_lookup ON deposits (method, amount_expected, cooldown_until);

INSERT INTO settings
    (key, default_value, value_type, min_value, max_value, category, label, help, sort_order)
VALUES
('binance_pay_enabled', 'no', 'bool', NULL, NULL, 'payments',
 'Binance Pay top-ups',
 'Reads your own Binance account''s Pay history and credits a customer''s '
 'wallet once a matching transfer is found. Needs BINANCE_UID, '
 'BINANCE_API_KEY and BINANCE_API_SECRET set in .env.', 400),

('binance_pay_tail_decimals', '4', 'int', 2, 4, 'payments',
 'Binance Pay fingerprint precision',
 'How many decimal places the unique amount uses. 4 means a request for 20 '
 'becomes 20.0037 — the fingerprint costs the customer under a cent. Drop '
 'to 2 only if Binance Pay ever refuses a four-decimal transfer.', 410),

('binance_pay_amount_cooldown_minutes', '180', 'int', 10, 20160, 'payments',
 'Binance Pay amount reservation (minutes)',
 'How long an expired request''s amount stays out of circulation. Shorter '
 'than the USDT equivalent by default, because a Binance Pay transfer '
 'settles instantly — there is no network confirmation delay to allow for.',
 420),

('binance_pay_lookback_minutes', '180', 'int', 10, 10080, 'payments',
 'How far back to search (minutes)',
 'The window of your own Binance Pay history searched on every sweep. It '
 'only needs to cover the life of the oldest open request — a narrower '
 'window is a cheaper, faster call.', 430),

('binance_pay_sweep_seconds', '20', 'int', 5, 600, 'payments',
 'Seconds between automatic checks',
 'How often the bot polls your Binance Pay history for new payments. '
 'Binance Pay settles instantly, so this can be short — the cost is one '
 'API call per sweep, covering every open request at once.', 440);
