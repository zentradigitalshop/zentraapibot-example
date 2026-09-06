"""The Telegram bot. Phase 1: catalogue, wallet balance, and buying with
whatever balance a customer already has.

NO PAYMENT RAIL CREDITS A WALLET YET. That is Phases 2 through 5 — USDT
BEP-20, Binance Pay, Telebirr and Bank of Abyssinia, one migration and one
module each, documented in docs/PAYMENTS.md as they land. Until then an
admin credits a customer by hand from the dashboard's Credit by hand page —
which is also the fallback every one of those rails keeps forever, exactly
as ZentraShopBot does, because a payment that does not match anything
automatic should never mean a customer simply loses their money.

WHERE THE MONEY ACTUALLY MOVES: purchase() below, and nowhere else. Every
button that can end in a charge calls this one function. A second
implementation of "debit, call Zentra, deliver or refund" is a second place
for those three steps to fall out of order.
"""

from __future__ import annotations

import asyncio
import html
import logging
import uuid
from decimal import Decimal

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

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


@dp.callback_query(F.data == "wallet")
async def wallet(call: CallbackQuery) -> None:
    user = await db.ensure_user(call.from_user.id, call.from_user.username)
    balance = await db.balance(user["id"])
    await call.message.edit_text(
        f"💳 <b>Your wallet</b>\n\nBalance: <b>{fmt_usdt(balance)}</b>\n\n"
        f"To top up, contact support — automatic top-ups are not wired up "
        f"in this starter yet. See docs/PAYMENTS.md.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button("« Menu", "home")]]),
    )
    await call.answer()


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


# ---- lifecycle -----------------------------------------------------------

async def main() -> None:
    await db.connect()
    await live.refresh()
    log.info("Connected. Markup: %s%%", live.get("markup_pct"))

    async def refresh_loop() -> None:
        while True:
            await asyncio.sleep(60)
            await live.refresh()

    asyncio.create_task(refresh_loop())

    try:
        await dp.start_polling(bot)
    finally:
        await zentra.aclose()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
