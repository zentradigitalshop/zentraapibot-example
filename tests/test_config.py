"""Config.load() and the properties that gate a payment rail.

No database, no network. Run:  python -m tests.test_config
"""

from __future__ import annotations

import os

REQUIRED = {
    "BOT_TOKEN": "123456789:AAEtestTokenForOfflineTestsOnly12345678",
    "ADMIN_IDS": "1,2",
    "ZENTRA_API_KEY": "zen_live_" + "a" * 40,
    "DATABASE_URL": "postgresql://user:pass@localhost/db",
}


def load_with(**overrides):
    saved = {k: os.environ.get(k) for k in list(REQUIRED) + list(overrides)}
    try:
        for key, value in REQUIRED.items():
            os.environ[key] = value
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        # Reload fresh each time — Config.load() reads os.environ directly,
        # so this is not a caching concern, just import hygiene.
        from bot.config import Config
        return Config.load()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    cfg = load_with()
    assert cfg.admin_ids == (1, 2)
    ok("comma-separated admin ids parse into a tuple of ints")

    cfg = load_with(ADMIN_IDS="1, notanumber, 3")
    assert cfg.admin_ids == (1, 3)
    ok("a non-numeric entry in ADMIN_IDS is dropped, not fatal")

    # ---- the USDT rail's enable condition -----------------------------------------------------

    print("\nWhen the USDT rail is considered configured")

    cfg = load_with()
    assert cfg.bsc_rpc_enabled is False
    ok("with nothing set, the rail is off")

    cfg = load_with(BSC_HTTP_URL="https://rpc.example", BSC_PAYMENT_ADDRESS="0xabc")
    assert cfg.bsc_rpc_enabled is True
    ok("BSC_HTTP_URL and BSC_PAYMENT_ADDRESS alone are enough — no WebSocket required")

    cfg = load_with(BSC_HTTP_URL="https://rpc.example")
    assert cfg.bsc_rpc_enabled is False
    ok("an HTTP endpoint with no payment address is still off")

    cfg = load_with(BSC_PAYMENT_ADDRESS="0xabc")
    assert cfg.bsc_rpc_enabled is False
    ok("a payment address with no HTTP endpoint is still off")

    # BSC_WSS_URL being present or absent must never change the answer —
    # this is the exact regression a "helpful" tidy-up could reintroduce.
    with_wss = load_with(BSC_HTTP_URL="https://rpc.example",
                         BSC_PAYMENT_ADDRESS="0xabc", BSC_WSS_URL="wss://rpc.example")
    without_wss = load_with(BSC_HTTP_URL="https://rpc.example",
                            BSC_PAYMENT_ADDRESS="0xabc", BSC_WSS_URL=None)
    assert with_wss.bsc_rpc_enabled == without_wss.bsc_rpc_enabled is True
    ok("BSC_WSS_URL makes no difference either way — it is read but not required")

    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    main()
