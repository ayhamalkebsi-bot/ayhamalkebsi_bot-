import os
from dataclasses import dataclass
from decimal import Decimal
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    shopdigital_api_key: str
    admin_id: int
    profit_margin: Decimal
    database_path: str
    shopdigital_base_url: str


def load_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    api_key = os.getenv("SHOPDIGITAL_API_KEY", "").strip()
    admin_id_raw = os.getenv("ADMIN_ID", "0").strip()
    margin_raw = os.getenv("PROFIT_MARGIN", "0.50").strip()
    database_path = os.getenv("DATABASE_PATH", "bot.db").strip()
    base_url = os.getenv(
        "SHOPDIGITAL_BASE_URL",
        "https://api.shopdigital.app",
    ).strip().rstrip("/")

    if not bot_token:
        raise RuntimeError("BOT_TOKEN غير موجود في ملف .env")

    if not api_key:
        raise RuntimeError("SHOPDIGITAL_API_KEY غير موجود في ملف .env")

    try:
        admin_id = int(admin_id_raw)
    except ValueError as exc:
        raise RuntimeError("ADMIN_ID يجب أن يكون رقمًا صحيحًا") from exc

    try:
        profit_margin = Decimal(margin_raw)
    except Exception as exc:
        raise RuntimeError("PROFIT_MARGIN غير صالح") from exc

    if profit_margin < 0:
        raise RuntimeError("PROFIT_MARGIN لا يمكن أن يكون سالبًا")

    return Settings(
        bot_token=bot_token,
        shopdigital_api_key=api_key,
        admin_id=admin_id,
        profit_margin=profit_margin,
        database_path=database_path,
        shopdigital_base_url=base_url,
    )
