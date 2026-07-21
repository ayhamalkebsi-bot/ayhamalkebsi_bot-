import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import load_settings
from database import Database
from payment_monitor import monitor_payments
from shopdigital import ShopDigitalClient
from handlers import admin, products, purchase, start, user


async def main() -> None:
    settings = load_settings()

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
    )

    db = Database(settings.database_path)
    await db.init()

    shop = ShopDigitalClient(
        settings.shopdigital_base_url,
        settings.shopdigital_api_key,
    )

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

    dp = Dispatcher()

    dp.include_router(
        start.register(settings, db)
    )
    dp.include_router(
        user.register(settings, db)
    )
    dp.include_router(
        products.register(settings, shop)
    )
    dp.include_router(
        purchase.register(settings, db, shop)
    )
    dp.include_router(
        admin.register(settings, db, shop)
    )

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    payment_monitor_task = asyncio.create_task(
        monitor_payments(
            bot=bot,
            db=db,
            shop=shop,
            settings=settings,
        )
    )

    try:
        await dp.start_polling(bot)
    finally:
        payment_monitor_task.cancel()

        try:
            await payment_monitor_task
        except asyncio.CancelledError:
            pass

        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
