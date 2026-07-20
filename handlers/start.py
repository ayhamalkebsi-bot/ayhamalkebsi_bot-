from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from config import Settings
from database import Database
from keyboards import main_menu

router = Router()


def register(settings: Settings, db: Database) -> Router:
    @router.message(CommandStart())
    async def start_handler(message: Message) -> None:
        user = message.from_user
        if user:
            await db.upsert_user(
                user_id=user.id,
                username=user.username,
                full_name=user.full_name,
            )

        await message.answer(
            "👋 أهلاً بك في متجر الاشتراكات الرقمية\n\n"
            "اختر من القائمة:",
            reply_markup=main_menu(
                is_admin=bool(
                    user and user.id == settings.admin_id
                )
            ),
        )

    return router
