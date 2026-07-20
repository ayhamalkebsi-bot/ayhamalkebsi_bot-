from __future__ import annotations

from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import Settings
from database import Database
from keyboards import admin_menu
from shopdigital import ShopDigitalClient, ShopDigitalError
from .common import money

router = Router()


class BalanceChange(StatesGroup):
    waiting_add = State()
    waiting_remove = State()


def register(
    settings: Settings,
    db: Database,
    shop: ShopDigitalClient,
) -> Router:
    async def ensure_admin(user_id: int) -> bool:
        return user_id == settings.admin_id

    @router.callback_query(F.data == "admin_panel")
    async def admin_panel_handler(callback: CallbackQuery) -> None:
        if not await ensure_admin(callback.from_user.id):
            await callback.answer("غير مصرح", show_alert=True)
            return

        await callback.answer()
        await callback.message.answer(
            "⚙️ لوحة الإدارة",
            reply_markup=admin_menu(),
        )

    @router.callback_query(F.data == "admin_add_balance")
    async def add_balance_start(
        callback: CallbackQuery,
        state: FSMContext,
    ) -> None:
        if not await ensure_admin(callback.from_user.id):
            await callback.answer("غير مصرح", show_alert=True)
            return

        await callback.answer()
        await state.set_state(BalanceChange.waiting_add)
        await callback.message.answer(
            "أرسل بالشكل التالي:\n"
            "<code>USER_ID AMOUNT</code>\n\n"
            "مثال:\n<code>123456789 25</code>",
            parse_mode="HTML",
        )

    @router.callback_query(F.data == "admin_remove_balance")
    async def remove_balance_start(
        callback: CallbackQuery,
        state: FSMContext,
    ) -> None:
        if not await ensure_admin(callback.from_user.id):
            await callback.answer("غير مصرح", show_alert=True)
            return

        await callback.answer()
        await state.set_state(BalanceChange.waiting_remove)
        await callback.message.answer(
            "أرسل بالشكل التالي:\n"
            "<code>USER_ID AMOUNT</code>\n\n"
            "مثال:\n<code>123456789 10</code>",
            parse_mode="HTML",
        )

    async def parse_balance_input(
        message: Message,
    ) -> tuple[int, Decimal] | None:
        parts = (message.text or "").split()

        if len(parts) != 2:
            await message.answer(
                "الصيغة غير صحيحة. استخدم:\n"
                "<code>USER_ID AMOUNT</code>",
                parse_mode="HTML",
            )
            return None

        try:
            user_id = int(parts[0])
            amount = Decimal(parts[1]).quantize(
                Decimal("0.01")
            )
        except (ValueError, InvalidOperation):
            await message.answer("القيم المدخلة غير صحيحة.")
            return None

        if amount <= 0:
            await message.answer("المبلغ يجب أن يكون أكبر من صفر.")
            return None

        return user_id, amount

    @router.message(BalanceChange.waiting_add)
    async def add_balance_finish(
        message: Message,
        state: FSMContext,
    ) -> None:
        if not await ensure_admin(message.from_user.id):
            await state.clear()
            return

        parsed = await parse_balance_input(message)
        if not parsed:
            return

        user_id, amount = parsed
        user = await db.get_user(user_id)

        if not user:
            await message.answer(
                "المستخدم غير موجود. يجب أن يضغط /start أولًا."
            )
            return

        new_balance = await db.change_balance(user_id, amount)
        await state.clear()

        await message.answer(
            f"✅ تمت إضافة ${money(amount)}\n"
            f"الرصيد الجديد: ${money(new_balance)}"
        )

        try:
            await message.bot.send_message(
                user_id,
                f"💵 تمت إضافة ${money(amount)} إلى رصيدك.\n"
                f"رصيدك الحالي: ${money(new_balance)}",
            )
        except Exception:
            pass

    @router.message(BalanceChange.waiting_remove)
    async def remove_balance_finish(
        message: Message,
        state: FSMContext,
    ) -> None:
        if not await ensure_admin(message.from_user.id):
            await state.clear()
            return

        parsed = await parse_balance_input(message)
        if not parsed:
            return

        user_id, amount = parsed
        user = await db.get_user(user_id)

        if not user:
            await message.answer(
                "المستخدم غير موجود. يجب أن يضغط /start أولًا."
            )
            return

        try:
            new_balance = await db.change_balance(
                user_id,
                -amount,
            )
        except ValueError:
            await message.answer("رصيد المستخدم غير كافٍ.")
            return

        await state.clear()

        await message.answer(
            f"✅ تم خصم ${money(amount)}\n"
            f"الرصيد الجديد: ${money(new_balance)}"
        )

    @router.callback_query(F.data == "admin_stats")
    async def admin_stats_handler(
        callback: CallbackQuery,
    ) -> None:
        if not await ensure_admin(callback.from_user.id):
            await callback.answer("غير مصرح", show_alert=True)
            return

        await callback.answer()
        stats = await db.stats()

        await callback.message.answer(
            "📊 إحصائيات المتجر\n\n"
            f"👥 المستخدمون: {stats['users_count']}\n"
            f"📦 الطلبات المكتملة: {stats['orders_count']}\n"
            f"💰 إجمالي المبيعات: ${money(stats['revenue'])}"
        )

    @router.callback_query(F.data == "provider_balance")
    async def provider_balance_handler(
        callback: CallbackQuery,
    ) -> None:
        if not await ensure_admin(callback.from_user.id):
            await callback.answer("غير مصرح", show_alert=True)
            return

        await callback.answer()

        try:
            result = await shop.balance()
        except ShopDigitalError as exc:
            await callback.message.answer(f"❌ {exc}")
            return

        balance = result.get(
            "balance",
            result.get("usdt_balance", result),
        )

        await callback.message.answer(
            f"💰 رصيد ShopDigital:\n{balance} USDT"
        )

    return router
