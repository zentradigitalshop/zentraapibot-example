"""The Telegram bot. Phase 1 added catalogue, wallet balance, and buying.
Phase 2 added automatic USDT (BEP-20) top-ups, watched on-chain. Phase 3
adds automatic Binance Pay top-ups, read from the operator's own account.

TELEBIRR AND BANK OF ABYSSINIA ARE NOT WIRED UP YET — Phase 5. Until then,
an admin credits a customer by hand from the dashboard's Credit by hand
page, which is also the fallback every rail keeps forever, exactly as
ZentraShopBot does, because a payment that does not match anything
automatic should never mean a customer simply loses their money.

WHERE THE MONEY ACTUALLY MOVES: purchase() below for spending; db.py's
credit_deposit() for every top-up rail, called from UsdtWatcher and
BinancePaySweeper alike — nowhere else. Every button that can end in a
charge calls one of exactly these paths.
"""

from __future__ import annotations

import asyncio
import html
import logging
import uuid
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .binance_pay import BinancePayClient, BinancePaySweeper
from .chain import BscRpc, UsdtWatcher
from .config import Config
from .db import Db
from .money import fmt_usdt
from .pricing import sell_price
from .settings import Settings
from .zentra_api import ZentraClient, ZentraError

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("bot")

cfg = Config.load()
db = Db(cfg.database_url)
live = Settings(db)
zentra = ZentraClient(cfg.zentra_api_key, base_url=cfg.zentra_api_base_url)

bot = Bot(token=cfg.bot_token)
dp = Dispatcher()

usdt_watcher: UsdtWatcher | None = None
_usdt_rpc: BscRpc | None = None
binance_sweeper: BinancePaySweeper | None = None
_binance_client: BinancePayClient | None = None


class TopUp(StatesGroup):
    usdt_amount = State()
    binance_amount = State()


def button(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [button("🛍 Shop", "shop")],
        [button("💳 Wallet", "wallet"), button("📦 My orders", "orders")],
    ])


# ---- purchase, the one function every buying button calls -----------------

class PurchaseError(Exception):
    def __init__(self, message: str, *, unresolved: bool = False):
        super().__init__(message)
        self.unresolved = unresolved


async def purchase(user_row: dict, product, quantity: int) -> dict:
    """Charge the customer, place the order with Zentra, deliver or refund.

    Mirrors ZentraShopBot's own orders.purchase(): debit BEFORE calling the
    supplier, refund on a clean failure, and NEVER refund on an unresolved
    one — Zentra was not sure the order went through, and a refund on top of
    a real charge is how a customer gets the product for free.
    """
    price = sell_price(product.price, live.decimal("markup_pct", "20"))
    total = price * quantity

    ok = await db.adjust_balance(user_row["id"], -total, "purchase")
    if not ok:
        balance = await db.balance(user_row["id"])
        raise PurchaseError(
            f"Not enough balance. This costs {fmt_usdt(total)} and you have "
            f"{fmt_usdt(balance)}. Use Wallet to top up."
        )

    # A key made ONCE, before Zentra is ever called, and stored with the
    # order before the request is sent. A retry — after a timeout, after
    # this process crashes mid-request — reuses this same key, so Zentra
    # answers with the ORIGINAL order rather than placing a second one.
    idem_key = f"resell-{uuid.uuid4()}"
    order_row = await db.create_pending_order(
        user_id=user_row["id"], zentra_product_id=product.id,
        product_name=product.name, quantity=quantity,
        price_snapshot=total, idempotency_key=idem_key,
    )

    try:
        result = await zentra.create_order(product.id, quantity, idempotency_key=idem_key)
    except ZentraError as exc:
        if exc.unresolved:
            # Zentra may or may not have delivered. Do NOT refund — that
            # would be a second payout if the order actually went through.
            # Hold it for a human; see docs/GUIDE.md's admin section.
            await db.mark_order_unresolved(order_row["id"], error=str(exc))
            raise PurchaseError(
                "Your order is being reviewed by our team and will be "
                "resolved shortly. This is not a request for another payment.",
                unresolved=True,
            ) from exc

        # A clean refusal from Zentra — nothing was charged on their side —
        # so the customer gets their money back.
        await db.adjust_balance(user_row["id"], total, "refund", idem_key)
        await db.mark_order_failed(order_row["id"], error=str(exc))
        raise PurchaseError(
            f"Could not complete your order: {exc.message}. "
            f"Your balance has not been touched."
        ) from exc

    delivered_json = None
    if result.delivery:
        import json
        delivered_json = json.dumps([{"label": d.label, "value": d.value} for d in result.delivery])

    await db.mark_order_delivered(
        order_row["id"], zentra_order_id=result.id,
        zentra_reference=result.reference, delivered_payload=delivered_json,
    )
    return {"order_id": order_row["id"], "delivery": result.delivery, "reference": result.reference}


# ---- screens -----------------------------------------------------------

@dp.message(Command("start"))
async def start(message: Message) -> None:
    await db.ensure_user(message.from_user.id, message.from_user.username)
    await message.answer(
        "👋 <b>Welcome!</b>\n\n"
        "Buy premium digital accounts, delivered the moment you pay.\n\n"
        "🛍 <b>Shop</b> — browse what's available\n"
        "💳 <b>Wallet</b> — your balance\n"
        "📦 <b>My orders</b> — anything you've bought",
        reply_markup=menu(),
    )


@dp.callback_query(F.data == "home")
async def home(call: CallbackQuery) -> None:
    await call.message.edit_text(
        "👋 <b>Welcome back.</b> Pick one below.",
        reply_markup=menu(),
    )
    await call.answer()


@dp.callback_query(F.data == "shop")
async def shop(call: CallbackQuery) -> None:
    try:
        products = await zentra.products()
    except ZentraError as exc:
        await call.answer(f"Could not load the shop: {exc.message}", show_alert=True)
        return

    available = [p for p in products if p.available]
    if not available:
        await call.message.edit_text(
            "Nothing is in stock right now — check back soon.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button("« Menu", "home")]]),
        )
        await call.answer()
        return

    rows = []
    for p in available:
        price = sell_price(p.price, live.decimal("markup_pct", "20"))
        label = f"{p.name} — {fmt_usdt(price)}"
        rows.append([button(label, f"p:{p.id}")])
    rows.append([button("« Menu", "home")])

    await call.message.edit_text(
        "🛍 <b>Shop</b>\n\nTap a product to buy it.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await call.answer()


@dp.callback_query(F.data.startswith("p:"))
async def product_screen(call: CallbackQuery) -> None:
    product_id = call.data.split(":", 1)[1]
    try:
        p = await zentra.product(product_id)
    except ZentraError as exc:
        await call.answer(exc.message, show_alert=True)
        return

    if not p.available:
        await call.answer("That product just sold out.", show_alert=True)
        return

    price = sell_price(p.price, live.decimal("markup_pct", "20"))
    text = (
        f"<b>{html.escape(p.name)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{html.escape(p.description)}\n\n"
        f"Price: <b>{fmt_usdt(price)}</b> / {html.escape(p.unit_label)}"
    )
    rows = [
        [button(f"Buy now — {fmt_usdt(price)}", f"buy:{p.id}:1")],
        [button("« Back to shop", "shop")],
    ]
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@dp.callback_query(F.data.startswith("buy:"))
async def buy(call: CallbackQuery) -> None:
    _, product_id, quantity_str = call.data.split(":")
    quantity = int(quantity_str)

    user = await db.ensure_user(call.from_user.id, call.from_user.username)

    try:
        p = await zentra.product(product_id)
    except ZentraError as exc:
        await call.answer(exc.message, show_alert=True)
        return

    if not p.available:
        await call.answer("That product just sold out.", show_alert=True)
        return

    await call.answer("Placing your order…")
    try:
        result = await purchase(user, p, quantity)
    except PurchaseError as exc:
        icon = "⏳" if exc.unresolved else "⚠️"
        await call.message.answer(f"{icon} {html.escape(str(exc))}")
        return

    lines = [f"✅ <b>Delivered!</b>  Order {result['reference']}", ""]
    if result["delivery"]:
        for field in result["delivery"]:
            lines.append(f"<b>{html.escape(field.label)}:</b> <code>{html.escape(field.value)}</code>")
    await call.message.answer("\n".join(lines))


def usdt_rail_live() -> bool:
    """Whether a customer can actually be offered USDT top-ups right now.

    Two things have to agree: the OPERATOR turned it on in the dashboard
    (`usdt_enabled`), and the DEPLOYMENT has somewhere to check payments
    against (`BSC_HTTP_URL` and `BSC_PAYMENT_ADDRESS` in .env). A dashboard
    switch with no watcher behind it would show a payment screen nothing is
    ever going to look at — so both must be true, not either.
    """
    return bool(live.get("usdt_enabled", False)) and cfg.bsc_rpc_enabled


def binance_rail_live() -> bool:
    """Same shape as usdt_rail_live(): the dashboard switch AND the
    deployment's own credentials (BINANCE_UID, BINANCE_API_KEY,
    BINANCE_API_SECRET in .env) both have to be true."""
    return bool(live.get("binance_pay_enabled", False)) and cfg.binance_pay_enabled


@dp.callback_query(F.data == "wallet")
async def wallet(call: CallbackQuery) -> None:
    user = await db.ensure_user(call.from_user.id, call.from_user.username)
    balance = await db.balance(user["id"])
    rows = []
    if usdt_rail_live():
        rows.append([button("➕ Top up with USDT (BEP-20)", "topup_usdt")])
    if binance_rail_live():
        rows.append([button("➕ Top up with Binance Pay", "topup_binance")])
    rows.append([button("« Menu", "home")])

    text = f"💳 <b>Your wallet</b>\n\nBalance: <b>{fmt_usdt(balance)}</b>"
    if not usdt_rail_live() and not binance_rail_live():
        text += ("\n\nTo top up, contact support — automatic top-ups "
                 "are not turned on yet.")

    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@dp.callback_query(F.data == "topup_usdt")
async def topup_usdt_start(call: CallbackQuery, state: FSMContext) -> None:
    if not usdt_rail_live():
        await call.answer("USDT top-ups are not turned on.", show_alert=True)
        return

    minimum = live.decimal("min_topup_usd", "1")
    await state.set_state(TopUp.usdt_amount)
    await call.message.edit_text(
        f"➕ <b>Top up with USDT</b>\n\n"
        f"How much do you want to add? Send a number — minimum {fmt_usdt(minimum)}.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button("« Cancel", "wallet")]]),
    )
    await call.answer()


@dp.message(TopUp.usdt_amount)
async def topup_usdt_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", "")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        await message.answer("That doesn't look like a number. Try again, or /start to cancel.")
        return

    minimum = live.decimal("min_topup_usd", "1")
    if amount < minimum:
        await message.answer(
            f"The smallest top-up is {fmt_usdt(minimum)}. Send a larger amount, "
            f"or /start to cancel."
        )
        return

    await state.clear()
    user = await db.ensure_user(message.from_user.id, message.from_user.username)

    try:
        deposit = await db.allocate_deposit(
            user_id=user["id"], base_amount=amount,
            window_minutes=int(live.get("deposit_window_minutes", 60)),
            cooldown_minutes=int(live.get("usdt_amount_cooldown_minutes", 1440)),
            tail_min=1, tail_max=99,
        )
    except RuntimeError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    exact = Decimal(deposit["amount_expected"])
    minutes = int(live.get("deposit_window_minutes", 60))
    await message.answer(
        f"💳 <b>Send exactly this much</b>\n\n"
        f"Network: <b>BNB Smart Chain (BEP-20) ONLY</b>\n\n"
        f"<code>{exact}</code> USDT\n\n"
        f"To this address:\n<code>{html.escape(cfg.bsc_payment_address)}</code>\n\n"
        f"⚠️ <b>Send exactly {exact}, not a rounded number.</b> Those last "
        f"digits are how this is recognised as yours — a rounded amount is "
        f"not detected automatically.\n\n"
        f"Your balance updates on its own once the network confirms it, "
        f"usually within a minute or two. You do not need to come back here.\n\n"
        f"<i>Valid for {minutes} minutes. Another network can lose the funds "
        f"permanently — BEP-20 only.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button("« Menu", "home")]]),
    )


@dp.callback_query(F.data == "topup_binance")
async def topup_binance_start(call: CallbackQuery, state: FSMContext) -> None:
    if not binance_rail_live():
        await call.answer("Binance Pay top-ups are not turned on.", show_alert=True)
        return

    minimum = live.decimal("min_topup_usd", "1")
    await state.set_state(TopUp.binance_amount)
    await call.message.edit_text(
        f"➕ <b>Top up with Binance Pay</b>\n\n"
        f"How much do you want to add? Send a number — minimum {fmt_usdt(minimum)}.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button("« Cancel", "wallet")]]),
    )
    await call.answer()


@dp.message(TopUp.binance_amount)
async def topup_binance_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", "")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        await message.answer("That doesn't look like a number. Try again, or /start to cancel.")
        return

    minimum = live.decimal("min_topup_usd", "1")
    if amount < minimum:
        await message.answer(
            f"The smallest top-up is {fmt_usdt(minimum)}. Send a larger amount, "
            f"or /start to cancel."
        )
        return

    await state.clear()
    user = await db.ensure_user(message.from_user.id, message.from_user.username)

    try:
        deposit = await db.allocate_deposit(
            user_id=user["id"], base_amount=amount, method="binancepay",
            window_minutes=int(live.get("deposit_window_minutes", 60)),
            cooldown_minutes=int(live.get("binance_pay_amount_cooldown_minutes", 180)),
            tail_min=1, tail_max=99,
            tail_decimals=int(live.get("binance_pay_tail_decimals", 4)),
        )
    except RuntimeError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    exact = Decimal(deposit["amount_expected"])
    minutes = int(live.get("deposit_window_minutes", 60))
    await message.answer(
        f"💳 <b>Send exactly this much</b>\n\n"
        f"Via <b>Binance Pay</b>, to Binance ID:\n<code>{html.escape(cfg.binance_uid)}</code>\n\n"
        f"<code>{exact}</code> USDT\n\n"
        f"⚠️ <b>Send exactly {exact}, not a rounded number.</b> Those last "
        f"digits are how this is recognised as yours — a rounded amount is "
        f"not detected automatically.\n\n"
        f"Your balance updates on its own once the transfer is found, "
        f"usually within a few seconds. You do not need to come back here.\n\n"
        f"<i>Valid for {minutes} minutes.</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button("« Menu", "home")]]),
    )


@dp.callback_query(F.data == "orders")
async def orders(call: CallbackQuery) -> None:
    user = await db.ensure_user(call.from_user.id, call.from_user.username)
    rows = await db.user_orders(user["id"], limit=10)
    if not rows:
        text = "📦 <b>Your orders</b>\n\nNothing here yet."
    else:
        lines = ["📦 <b>Your orders</b>", ""]
        for row in rows:
            lines.append(
                f"#{row['id']} {html.escape(row['product_name'])} × {row['quantity']} "
                f"— {fmt_usdt(Decimal(row['price_snapshot']))} — {row['status']}"
            )
        text = "\n".join(lines)
    await call.message.edit_text(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button("« Menu", "home")]]))
    await call.answer()


async def notify_credited(deposit: dict) -> None:
    """Tell a customer their top-up landed, on whichever rail credited it.
    Runs AFTER the balance is already updated — this never decides whether
    to credit anything, only whether the customer hears about it."""
    user = await db.user_by_id(deposit["user_id"])
    if user is None:
        return
    try:
        await bot.send_message(
            user["telegram_id"],
            f"✅ <b>Top-up received!</b>\n\n"
            f"+{fmt_usdt(Decimal(deposit['amount_credited']))} added to your wallet.",
        )
    except Exception:  # noqa: BLE001
        # A customer who blocked the bot, or a Telegram hiccup, must not
        # take the watcher down — their balance is already correct either
        # way; only the notification failed.
        log.exception("Could not notify user %s of their credited deposit.",
                      user["telegram_id"])


# ---- lifecycle -----------------------------------------------------------

async def main() -> None:
    global usdt_watcher, _usdt_rpc, binance_sweeper, _binance_client

    await db.connect()
    await live.refresh()
    log.info("Connected. Markup: %s%%", live.get("markup_pct"))

    async def refresh_loop() -> None:
        while True:
            await asyncio.sleep(60)
            await live.refresh()

    asyncio.create_task(refresh_loop())

    watcher_task = None
    if cfg.bsc_rpc_enabled:
        # Started whenever the DEPLOYMENT is configured for it, regardless
        # of the dashboard's usdt_enabled switch — the switch only decides
        # whether a CUSTOMER is offered the top-up screen; the watcher
        # itself does no harm running with nothing to match against, and
        # starting it unconditionally means flipping the setting on takes
        # effect immediately rather than needing a restart.
        _usdt_rpc = BscRpc(cfg.bsc_http_url)
        usdt_watcher = UsdtWatcher(
            db, _usdt_rpc, token_address=cfg.usdt_contract_address,
            payment_address=cfg.bsc_payment_address,
            confirmations=int(live.get("usdt_confirmations", 3)),
            on_credit=notify_credited,
        )
        watcher_task = asyncio.create_task(usdt_watcher.run_forever())
        log.info("USDT watcher starting: address=%s", cfg.bsc_payment_address)
    else:
        log.info("USDT watcher not started — BSC_HTTP_URL/BSC_PAYMENT_ADDRESS "
                 "not fully configured in .env.")

    sweeper_task = None
    if cfg.binance_pay_enabled:
        # Same reasoning as the USDT watcher above: started whenever the
        # DEPLOYMENT has credentials, independent of the dashboard switch.
        _binance_client = BinancePayClient(
            cfg.binance_uid, cfg.binance_api_key, cfg.binance_api_secret,
            base_url=cfg.binance_api_base,
        )
        binance_sweeper = BinancePaySweeper(
            db, _binance_client,
            lookback_minutes=int(live.get("binance_pay_lookback_minutes", 180)),
            sweep_seconds=int(live.get("binance_pay_sweep_seconds", 20)),
            on_credit=notify_credited,
        )
        sweeper_task = asyncio.create_task(binance_sweeper.run_forever())
        log.info("Binance Pay sweeper starting: uid=%s", cfg.binance_uid)
    else:
        log.info("Binance Pay sweeper not started — BINANCE_UID/BINANCE_API_KEY/"
                 "BINANCE_API_SECRET not fully configured in .env.")

    try:
        await dp.start_polling(bot)
    finally:
        if usdt_watcher is not None:
            usdt_watcher.stop()
        if watcher_task is not None:
            watcher_task.cancel()
        if _usdt_rpc is not None:
            await _usdt_rpc.aclose()
        if binance_sweeper is not None:
            binance_sweeper.stop()
        if sweeper_task is not None:
            sweeper_task.cancel()
        if _binance_client is not None:
            await _binance_client.aclose()
        await zentra.aclose()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
