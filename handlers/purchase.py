from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery

from config import Settings
from database import Database
from keyboards import confirm_purchase_keyboard
from shopdigital import ShopDigitalClient, ShopDigitalError
from .common import calculate_sale_price, money, safe_text

router = Router()
_purchase_locks: dict[int, asyncio.Lock] = {}


def register(
    settings: Settings,
    db: Database,
    shop: ShopDigitalClient,
) -> Router:
    @router.callback_query(F.data.startswith("buy:"))
    async def buy_preview_handler(callback: CallbackQuery) -> None:
        await callback.answer()

        product_id = callback.data.split(":", 1)[1]

        try:
            products = await shop.products()
        except ShopDigitalError as exc:
            await callback.message.answer(f"❌ {exc}")
            return

        product = next(
            (
                item
                for item in products
                if str(item.get("id")) == product_id
            ),
            None,
        )

        if not product:
            await callback.message.answer("المنتج غير موجود.")
            return

        stock = int(product.get("stock", 0) or 0)
        if stock < 1:
            await callback.message.answer("المنتج نفد من المخزون.")
            return

        name = safe_text(product.get("name"), "منتج")
        sale_price = calculate_sale_price(
            product.get("price", 0),
            settings.profit_margin,
        )
        user_balance = await db.get_balance(callback.from_user.id)

        await callback.message.answer(
            f"🛍 المنتج: {name}\n"
            f"💰 السعر: ${money(sale_price)}\n"
            f"💵 رصيدك: ${money(user_balance)}\n\n"
            "هل تريد تأكيد الشراء؟",
            reply_markup=confirm_purchase_keyboard(product_id),
        )

    @router.callback_query(F.data == "cancel_buy")
    async def cancel_handler(callback: CallbackQuery) -> None:
        await callback.answer("تم الإلغاء")
        await callback.message.answer("❌ تم إلغاء العملية.")

    @router.callback_query(F.data.startswith("confirm_buy:"))
    async def confirm_handler(callback: CallbackQuery) -> None:
        await callback.answer("جاري تنفيذ الطلب...")

        user_id = callback.from_user.id
        lock = _purchase_locks.setdefault(user_id, asyncio.Lock())

        if lock.locked():
            await callback.message.answer(
                "لديك عملية شراء قيد التنفيذ، انتظر قليلًا."
            )
            return

        async with lock:
            product_id = callback.data.split(":", 1)[1]

            try:
                products = await shop.products()
            except ShopDigitalError as exc:
                await callback.message.answer(f"❌ {exc}")
                return

            product = next(
                (
                    item
                    for item in products
                    if str(item.get("id")) == product_id
                ),
                None,
            )

            if not product:
                await callback.message.answer("المنتج غير موجود.")
                return

            if int(product.get("stock", 0) or 0) < 1:
                await callback.message.answer(
                    "المنتج نفد من المخزون."
                )
                return

            name = safe_text(product.get("name"), "منتج")
            provider_price = Decimal(
                str(product.get("price", 0))
            ).quantize(Decimal("0.01"))
            sale_price = calculate_sale_price(
                provider_price,
                settings.profit_margin,
            )

            balance = await db.get_balance(user_id)
            if balance < sale_price:
                await callback.message.answer(
                    f"❌ رصيدك غير كافٍ.\n"
                    f"المطلوب: ${money(sale_price)}\n"
                    f"رصيدك: ${money(balance)}"
                )
                return

            external_order_id = (
                f"tg-{user_id}-{uuid.uuid4().hex}"
            )

            await db.create_pending_order(
                external_order_id=external_order_id,
                user_id=user_id,
                product_id=product_id,
                product_name=name,
                provider_price=provider_price,
                sale_price=sale_price,
                quantity=1,
            )

            try:
                await db.change_balance(user_id, -sale_price)
            except ValueError:
                await db.mark_order_failed(
                    external_order_id,
                    "تعذر خصم رصيد المستخدم",
                )
                await callback.message.answer(
                    "❌ تعذر خصم الرصيد."
                )
                return

            try:
                result = await shop.purchase(
                    product_id=product_id,
                    quantity=1,
                    external_order_id=external_order_id,
                )
            except ShopDigitalError as exc:
                await db.change_balance(user_id, sale_price)
                await db.mark_order_failed(
                    external_order_id,
                    str(exc),
                )
                await callback.message.answer(
                    f"❌ فشل الطلب وتمت إعادة رصيدك.\n{exc}"
                )
                return
            except Exception as exc:
                await db.change_balance(user_id, sale_price)
                await db.mark_order_failed(
                    external_order_id,
                    f"خطأ غير متوقع: {exc}",
                )
                await callback.message.answer(
                    "❌ حدث خطأ غير متوقع وتمت إعادة رصيدك."
                )
                return

            credentials = safe_text(
                result.get("credentials"),
                "لم يرسل المزود بيانات دخول",
            )
            provider_order_id = safe_text(
                result.get("order_id"),
                external_order_id,
            )

            await db.mark_order_success(
                external_order_id,
                provider_order_id,
                credentials,
            )

            new_balance = await db.get_balance(user_id)

            await callback.message.answer(
                "✅ تم تنفيذ طلبك بنجاح\n\n"
                f"🛍 المنتج: {name}\n"
                f"💰 السعر: ${money(sale_price)}\n"
                f"🧾 رقم الطلب: {provider_order_id}\n"
                f"💵 رصيدك المتبقي: ${money(new_balance)}\n\n"
                f"🔐 بيانات الاشتراك:\n<code>{credentials}</code>",
                parse_mode="HTML",
            )

    return router
