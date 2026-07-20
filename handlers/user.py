from aiogram import F, Router
from aiogram.types import CallbackQuery

from config import Settings
from database import Database
from keyboards import main_menu
from .common import money

router = Router()


def register(settings: Settings, db: Database) -> Router:
    @router.callback_query(F.data == "balance")
    async def balance_handler(callback: CallbackQuery) -> None:
        await callback.answer()
        if not callback.from_user:
            return
        balance = await db.get_balance(callback.from_user.id)
        await callback.message.answer(
            f"💵 رصيدك الحالي: ${money(balance)}"
        )

    @router.callback_query(F.data == "my_orders")
    async def orders_handler(callback: CallbackQuery) -> None:
        await callback.answer()
        if not callback.from_user:
            return

        orders = await db.get_user_orders(
            callback.from_user.id,
            limit=10,
        )

        if not orders:
            await callback.message.answer("لا توجد لديك طلبات بعد.")
            return

        lines = ["📜 آخر طلباتك:\n"]
        status_map = {
            "pending": "قيد المعالجة",
            "completed": "مكتمل",
            "failed": "فشل",
        }

        for order in orders:
            lines.append(
                f"• {order['product_name']}\n"
                f"  السعر: ${order['sale_price']}\n"
                f"  الحالة: {status_map.get(order['status'], order['status'])}\n"
                f"  رقمك المرجعي: {order['external_order_id']}\n"
            )

        await callback.message.answer("\n".join(lines))

    @router.callback_query(F.data == "back_main")
    async def back_main_handler(callback: CallbackQuery) -> None:
        await callback.answer()
        await callback.message.answer(
            "القائمة الرئيسية:",
            reply_markup=main_menu(
                is_admin=callback.from_user.id == settings.admin_id
            ),
        )

    return router
