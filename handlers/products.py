from aiogram import F, Router
from aiogram.types import CallbackQuery

from config import Settings
from keyboards import product_keyboard
from shopdigital import ShopDigitalClient, ShopDigitalError
from .common import calculate_sale_price, money, safe_text

router = Router()


def register(
    settings: Settings,
    shop: ShopDigitalClient,
) -> Router:
    @router.callback_query(F.data == "products")
    async def products_handler(callback: CallbackQuery) -> None:
        await callback.answer("جاري تحميل المنتجات...")

        try:
            products = await shop.products()
        except ShopDigitalError as exc:
            await callback.message.answer(f"❌ {exc}")
            return

        available = [
            product
            for product in products
            if int(product.get("stock", 0) or 0) > 0
        ]

        if not available:
            await callback.message.answer(
                "لا توجد منتجات متوفرة حاليًا."
            )
            return

        await callback.message.answer(
            f"🛍 المنتجات المتوفرة: {len(available)}"
        )

        for product in available[:50]:
            product_id = safe_text(product.get("id"))
            name = safe_text(product.get("name"), "منتج")
            stock = product.get("stock", 0)
            provider_price = product.get("price", 0)
            sale_price = calculate_sale_price(
                provider_price,
                settings.profit_margin,
            )

            await callback.message.answer(
                f"🛍 {name}\n"
                f"💰 السعر: ${money(sale_price)}\n"
                f"📦 المتوفر: {stock}",
                reply_markup=product_keyboard(
                    product_id,
                    money(sale_price),
                ),
            )

    return router
