from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from config import Settings
from database import Database
from shopdigital import ShopDigitalClient, ShopDigitalError
from .common import calculate_sale_price, money, safe_text


router = Router()
_purchase_locks: dict[int, asyncio.Lock] = {}


def payment_method_keyboard(
    product_id: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💵 الدفع من الرصيد",
                    callback_data=f"pay_balance:{product_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Pay with BEP20",
                    callback_data=f"pay_bep20:{product_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ إلغاء",
                    callback_data="cancel_buy",
                )
            ],
        ]
    )


async def get_product(
    shop: ShopDigitalClient,
    product_id: str,
) -> dict | None:
    products = await shop.products()

    return next(
        (
            item
            for item in products
            if str(item.get("id")) == product_id
        ),
        None,
    )


def register(
    settings: Settings,
    db: Database,
    shop: ShopDigitalClient,
) -> Router:

    @router.callback_query(F.data.startswith("buy:"))
    async def buy_preview_handler(
        callback: CallbackQuery,
    ) -> None:
        await callback.answer()

        product_id = callback.data.split(":", 1)[1]

        try:
            product = await get_product(shop, product_id)
        except ShopDigitalError as exc:
            await callback.message.answer(f"❌ {exc}")
            return

        if not product:
            await callback.message.answer(
                "❌ المنتج غير موجود."
            )
            return

        stock = int(product.get("stock", 0) or 0)

        if stock < 1:
            await callback.message.answer(
                "❌ المنتج نفد من المخزون."
            )
            return

        name = safe_text(
            product.get("name"),
            "منتج",
        )

        sale_price = calculate_sale_price(
            product.get("price", 0),
            settings.profit_margin,
        )

        user_balance = await db.get_balance(
            callback.from_user.id
        )

        await callback.message.answer(
            f"🛍 المنتج: {name}\n"
            f"💰 السعر: {money(sale_price)} USDT\n"
            f"💵 رصيدك: {money(user_balance)} USDT\n\n"
            "اختر وسيلة الدفع:",
            reply_markup=payment_method_keyboard(product_id),
        )

    @router.callback_query(F.data == "cancel_buy")
    async def cancel_handler(
        callback: CallbackQuery,
    ) -> None:
        await callback.answer("تم الإلغاء")

        await callback.message.answer(
            "❌ تم إلغاء العملية."
        )

    @router.callback_query(
        F.data.startswith("pay_bep20:")
    )
    async def pay_bep20_handler(
        callback: CallbackQuery,
    ) -> None:
        await callback.answer(
            "جاري إنشاء طلب الدفع..."
        )

        user_id = callback.from_user.id
        product_id = callback.data.split(":", 1)[1]

        lock = _purchase_locks.setdefault(
            user_id,
            asyncio.Lock(),
        )

        if lock.locked():
            await callback.message.answer(
                "لديك عملية قيد التنفيذ، انتظر قليلًا."
            )
            return

        async with lock:
            try:
                product = await get_product(
                    shop,
                    product_id,
                )
            except ShopDigitalError as exc:
                await callback.message.answer(
                    f"❌ {exc}"
                )
                return

            if not product:
                await callback.message.answer(
                    "❌ المنتج غير موجود."
                )
                return

            stock = int(
                product.get("stock", 0) or 0
            )

            if stock < 1:
                await callback.message.answer(
                    "❌ المنتج نفد من المخزون."
                )
                return

            name = safe_text(
                product.get("name"),
                "منتج",
            )

            provider_price = Decimal(
                str(product.get("price", 0))
            ).quantize(Decimal("0.01"))

            sale_price = calculate_sale_price(
                provider_price,
                settings.profit_margin,
            )

            external_order_id = (
                f"tg-{user_id}-{uuid.uuid4().hex}"
            )

            try:
                await db.create_pending_order(
                    external_order_id=external_order_id,
                    user_id=user_id,
                    product_id=product_id,
                    product_name=name,
                    provider_price=provider_price,
                    sale_price=sale_price,
                    quantity=1,
                )

                payment_amount = (
                    await db.create_usdt_payment(
                        external_order_id=(
                            external_order_id
                        ),
                        user_id=user_id,
                        wallet_address=(
                            settings.usdt_wallet
                        ),
                        base_amount=sale_price,
                        expiration_minutes=30,
                    )
                )

            except Exception as exc:
                try:
                    await db.mark_order_failed(
                        external_order_id,
                        f"تعذر إنشاء الدفع: {exc}",
                    )
                except Exception:
                    pass

                await callback.message.answer(
                    "❌ تعذر إنشاء طلب الدفع.\n"
                    "حاول مرة أخرى لاحقًا."
                )
                return

            await callback.message.answer(
                "💳 <b>Pay with USDT (BEP20)</b>\n\n"
                f"🛍 المنتج: {name}\n\n"
                "💰 أرسل المبلغ التالي بالضبط:\n"
                f"<code>{payment_amount}</code> USDT\n\n"
                "🌐 الشبكة:\n"
                "<b>BNB Smart Chain (BEP20)</b>\n\n"
                "📬 عنوان المحفظة:\n"
                f"<code>{settings.usdt_wallet}</code>\n\n"
                "⚠️ أرسل المبلغ نفسه دون زيادة "
                "أو نقصان.\n"
                "⚠️ استخدم شبكة BEP20 فقط.\n"
                "⏳ تنتهي مهلة الدفع بعد 30 دقيقة.\n\n"
                f"🧾 رقم الطلب:\n"
                f"<code>{external_order_id}</code>",
                parse_mode="HTML",
            )

    @router.callback_query(
        F.data.startswith("pay_balance:")
    )
    async def pay_balance_handler(
        callback: CallbackQuery,
    ) -> None:
        await callback.answer(
            "جاري تنفيذ الطلب..."
        )

        user_id = callback.from_user.id
        product_id = callback.data.split(":", 1)[1]

        lock = _purchase_locks.setdefault(
            user_id,
            asyncio.Lock(),
        )

        if lock.locked():
            await callback.message.answer(
                "لديك عملية شراء قيد التنفيذ، "
                "انتظر قليلًا."
            )
            return

        async with lock:
            try:
                product = await get_product(
                    shop,
                    product_id,
                )
            except ShopDigitalError as exc:
                await callback.message.answer(
                    f"❌ {exc}"
                )
                return

            if not product:
                await callback.message.answer(
                    "❌ المنتج غير موجود."
                )
                return

            stock = int(
                product.get("stock", 0) or 0
            )

            if stock < 1:
                await callback.message.answer(
                    "❌ المنتج نفد من المخزون."
                )
                return

            name = safe_text(
                product.get("name"),
                "منتج",
            )

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
                    "❌ رصيدك غير كافٍ.\n"
                    f"المطلوب: {money(sale_price)} USDT\n"
                    f"رصيدك: {money(balance)} USDT\n\n"
                    "يمكنك اختيار Pay with BEP20 "
                    "بدلًا من الرصيد."
                )
                return

            external_order_id = (
                f"tg-{user_id}-{uuid.uuid4().hex}"
            )

            try:
                await db.create_pending_order(
                    external_order_id=external_order_id,
                    user_id=user_id,
                    product_id=product_id,
                    product_name=name,
                    provider_price=provider_price,
                    sale_price=sale_price,
                    quantity=1,
                )

                await db.change_balance(
                    user_id,
                    -sale_price,
                )

                await db.mark_order_processing(
                    external_order_id
                )

            except ValueError:
                await db.mark_order_failed(
                    external_order_id,
                    "تعذر خصم رصيد المستخدم",
                )

                await callback.message.answer(
                    "❌ تعذر خصم الرصيد."
                )
                return

            except Exception as exc:
                await db.mark_order_failed(
                    external_order_id,
                    f"خطأ في إنشاء الطلب: {exc}",
                )

                await callback.message.answer(
                    "❌ تعذر إنشاء الطلب."
                )
                return

            try:
                result = await shop.purchase(
                    product_id=product_id,
                    quantity=1,
                    external_order_id=external_order_id,
                )

            except ShopDigitalError as exc:
                await db.change_balance(
                    user_id,
                    sale_price,
                )

                await db.mark_order_failed(
                    external_order_id,
                    str(exc),
                )

                await callback.message.answer(
                    "❌ فشل تنفيذ الطلب وتمت "
                    f"إعادة رصيدك.\n{exc}"
                )
                return

            except Exception as exc:
                await db.change_balance(
                    user_id,
                    sale_price,
                )

                await db.mark_order_failed(
                    external_order_id,
                    f"خطأ غير متوقع: {exc}",
                )

                await callback.message.answer(
                    "❌ حدث خطأ غير متوقع وتمت "
                    "إعادة رصيدك."
                )
                return

            credentials = safe_text(
                result.get("credentials"),
                "لم يرسل المزود بيانات الدخول",
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

            new_balance = await db.get_balance(
                user_id
            )

            await callback.message.answer(
                "✅ تم تنفيذ طلبك بنجاح\n\n"
                f"🛍 المنتج: {name}\n"
                f"💰 السعر: "
                f"{money(sale_price)} USDT\n"
                f"🧾 رقم الطلب: "
                f"{provider_order_id}\n"
                f"💵 رصيدك المتبقي: "
                f"{money(new_balance)} USDT\n\n"
                "🔐 بيانات الاشتراك:\n"
                f"<code>{credentials}</code>",
                parse_mode="HTML",
            )

    return router
