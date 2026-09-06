"""The /wallet -> top up -> deposit screen flow, through the real handlers.

Not the watcher itself (see test_watcher.py) — this is what a customer
actually sees and types, with a fake Telegram message/callback standing in
for aiogram's own objects.

Run:  TEST_DATABASE_URL=postgresql://… python -m tests.test_topup_flow
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal

os.environ.setdefault("BOT_TOKEN", "123456789:AAEtestTokenForOfflineTestsOnly12345678")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("ZENTRA_API_KEY", "zen_live_testtesttesttesttesttesttesttest01")

from tests.dbfixture import fresh_db, test_database_url  # noqa: E402

os.environ["DATABASE_URL"] = test_database_url()

from bot import bot as botmod  # noqa: E402
from bot.bot import (  # noqa: E402
    TopUp, topup_binance_amount, topup_binance_start, topup_usdt_amount,
    topup_usdt_start, wallet,
)


class FakeState:
    """Stands in for aiogram's FSMContext — just enough to track one state."""

    def __init__(self):
        self._state = None

    async def set_state(self, state) -> None:
        self._state = state

    async def clear(self) -> None:
        self._state = None

    @property
    def state(self):
        return self._state


def _button_labels(markup) -> list[str]:
    if markup is None:
        return []
    return [b.text for row in markup.inline_keyboard for b in row]


class FakeMessage:
    def __init__(self, chat_id: int, text: str = ""):
        self.chat = type("C", (), {"id": chat_id})()
        self.text = text
        self.from_user = type("U", (), {"id": chat_id, "username": f"user{chat_id}"})()
        self.sent: list[str] = []
        self.edited: list[str] = []
        # Buttons live in reply_markup, not in the text — a screen showing
        # "Top up with USDT" is a button, never a string edit_text() was
        # called with, so checking .edited alone cannot see it.
        self.edited_markups: list = []

    async def answer(self, text, **kwargs):
        self.sent.append(text)

    async def edit_text(self, text, **kwargs):
        self.edited.append(text)
        self.edited_markups.append(kwargs.get("reply_markup"))


class FakeCall:
    def __init__(self, telegram_id: int):
        self.data = ""
        self.from_user = type("U", (), {"id": telegram_id, "username": f"user{telegram_id}"})()
        self.message = FakeMessage(telegram_id)
        self.alerts: list[str] = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        if text:
            self.alerts.append(text)


async def main() -> None:
    checks: list[str] = []

    def ok(label: str) -> None:
        checks.append(label)
        print(f"  ✓ {label}")

    db = await fresh_db()
    from bot.settings import Settings
    botmod.db = db
    botmod.live = Settings(db)
    await botmod.live.refresh()

    # ---- the rail is off by default -----------------------------------------------------

    print("\nOff by default")

    call = FakeCall(5001)
    await wallet(call)
    labels = _button_labels(call.message.edited_markups[-1])
    assert not any("USDT" in label for label in labels), labels
    ok("with usdt_enabled at its shipped default (no), the wallet screen offers no USDT button")

    call2 = FakeCall(5001)
    await topup_usdt_start(call2, FakeState())
    assert call2.alerts and "not turned on" in call2.alerts[0]
    ok("and tapping the callback directly is refused with a clear reason, not a crash")

    # ---- turn it on (deployment side is faked; dashboard side is real) -----------------------------------------------------

    print("\nWith the rail live")

    await db.write_setting("usdt_enabled", "yes", updated_by=None)
    await botmod.live.refresh()
    # The deployment side (BSC_HTTP_URL, BSC_PAYMENT_ADDRESS in .env) is not
    # configurable from a test env var already loaded at import time, so the
    # module-level cfg is patched directly — this test is about the
    # SCREENS. bsc_rpc_enabled needs only these two; BSC_WSS_URL is reserved
    # for a future upgrade and is not read anywhere today.
    botmod.cfg = botmod.cfg.__class__(
        **{**botmod.cfg.__dict__, "bsc_http_url": "https://fake",
           "bsc_payment_address": "0x1111111111111111111111111111111111111111"})

    call3 = FakeCall(5002)
    await wallet(call3)
    labels = _button_labels(call3.message.edited_markups[-1])
    assert any("USDT" in label for label in labels), labels
    ok("with the setting on and the deployment configured, the button appears")

    state = FakeState()
    call4 = FakeCall(5002)
    await topup_usdt_start(call4, state)
    assert state.state == TopUp.usdt_amount
    ok("tapping it puts the conversation into the amount-entry state")

    # ---- entering an amount -----------------------------------------------------

    print("\nEntering an amount")

    bad = FakeMessage(5003, text="not a number")
    await topup_usdt_amount(bad, FakeState())
    assert "doesn't look like a number" in bad.sent[-1]
    ok("junk input gets a plain correction, not a crash")

    too_small = FakeMessage(5004, text="0.01")
    await topup_usdt_amount(too_small, FakeState())
    assert "smallest top-up" in too_small.sent[-1]
    ok("an amount below the minimum is refused with the actual minimum stated")

    good_state = FakeState()
    good = FakeMessage(5005, text="5")
    await topup_usdt_amount(good, good_state)
    assert good_state.state is None, "the state was not cleared after a successful allocation"
    ok("a valid amount clears the conversation state")

    reply = good.sent[-1]
    assert "Send exactly this much" in reply
    assert "0x1111111111111111111111111111111111111111" in reply
    ok("the reply shows the payment address and says to send the exact figure")

    deposits = await db.user_deposits((await db.user_by_telegram_id(5005))["id"])
    assert len(deposits) == 1
    exact = Decimal(deposits[0]["amount_expected"])
    assert Decimal("5.00") <= exact < Decimal("5.01")
    assert str(exact) in reply, "the screen's figure does not match the allocated deposit"
    ok("and the figure shown is the exact figure that was actually reserved")

    # A comma in the input (people paste "1,000") must not break parsing.
    comma_state = FakeState()
    comma = FakeMessage(5006, text="1,000")
    await topup_usdt_amount(comma, comma_state)
    assert "Send exactly this much" in comma.sent[-1]
    ok("a comma-formatted amount is accepted")

    # ---- Binance Pay: off, then on -----------------------------------------------------

    print("\nBinance Pay, off by default")

    call5 = FakeCall(5007)
    await wallet(call5)
    labels = _button_labels(call5.message.edited_markups[-1])
    assert not any("Binance Pay" in label for label in labels), labels
    ok("with binance_pay_enabled at its shipped default (no), no Binance Pay button appears")

    call6 = FakeCall(5007)
    await topup_binance_start(call6, FakeState())
    assert call6.alerts and "not turned on" in call6.alerts[0]
    ok("and tapping the callback directly is refused with a clear reason")

    print("\nBinance Pay, live")

    await db.write_setting("binance_pay_enabled", "yes", updated_by=None)
    await botmod.live.refresh()
    botmod.cfg = botmod.cfg.__class__(
        **{**botmod.cfg.__dict__, "binance_uid": "732609210",
           "binance_api_key": "key", "binance_api_secret": "secret"})

    call7 = FakeCall(5008)
    await wallet(call7)
    labels = _button_labels(call7.message.edited_markups[-1])
    assert any("Binance Pay" in label for label in labels), labels
    # And the USDT button, from earlier in this test, is STILL there —
    # turning one rail on must not turn the other off.
    assert any("USDT" in label for label in labels), labels
    ok("both rails' buttons appear together, independently of one another")

    binance_state = FakeState()
    binance_msg = FakeMessage(5008, text="9")
    await topup_binance_start(FakeCall(5008), binance_state)
    await topup_binance_amount(binance_msg, binance_state)
    reply = binance_msg.sent[-1]
    assert "Binance Pay" in reply and "732609210" in reply
    ok("the Binance Pay deposit screen shows the operator's own Binance ID")

    binance_deposits = [
        d for d in await db.user_deposits((await db.user_by_telegram_id(5008))["id"])
        if d["method"] == "binancepay"
    ]
    assert len(binance_deposits) == 1
    ok("and the deposit is recorded under method='binancepay'")

    await db.close()
    print(f"\n{len(checks)} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
