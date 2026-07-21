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
    usdt_wallet: str
    bsc_rpc_url: str


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

    usdt_wallet = os.getenv("USDT_WALLET", "").strip()
    bsc_rpc_url = os.getenv("BSC_RPC_URL", "").strip()

    if not bot_token:
        raise RuntimeError(
            "BOT_TOKEN غير موجود في متغيرات البيئة"
        )

    if not api_key:
        raise RuntimeError(
            "SHOPDIGITAL_API_KEY غير موجود في متغيرات البيئة"
        )

    try:
        admin_id = int(admin_id_raw)
    except ValueError as exc:
        raise RuntimeError(
            "ADMIN_ID يجب أن يكون رقمًا صحيحًا"
        ) from exc

    try:
        profit_margin = Decimal(margin_raw)
    except Exception as exc:
        raise RuntimeError(
            "PROFIT_MARGIN غير صالح"
        ) from exc

    if profit_margin < 0:
        raise RuntimeError(
            "PROFIT_MARGIN لا يمكن أن يكون سالبًا"
        )

    if (
        not usdt_wallet.startswith("0x")
        or len(usdt_wallet) != 42
    ):
        raise RuntimeError(
            "USDT_WALLET ليس عنوان BEP20 صالحًا"
        )

    if not bsc_rpc_url:
        raise RuntimeError(
            "BSC_RPC_URL غير موجود في متغيرات البيئة"
        )

    if not bsc_rpc_url.startswith(("https://", "http://")):
        raise RuntimeError(
            "BSC_RPC_URL يجب أن يكون رابط HTTPS أو HTTP"
        )

    return Settings(
        bot_token=bot_token,
        shopdigital_api_key=api_key,
        admin_id=admin_id,
        profit_margin=profit_margin,
        database_path=database_path,
        shopdigital_base_url=base_url,
        usdt_wallet=usdt_wallet,
        bsc_rpc_url=bsc_rpc_url,
    )
