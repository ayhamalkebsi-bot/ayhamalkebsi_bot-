from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import aiohttp
from aiogram import Bot

from config import Settings
from database import Database
from shopdigital import ShopDigitalClient, ShopDigitalError
from handlers.common import safe_text


logger = logging.getLogger(__name__)

# Binance-Peg BSC-USD / USDT على شبكة BSC.
USDT_CONTRACT = (
    "0x55d398326f99059ff775485246999027b3197955"
)

# keccak256("Transfer(address,address,uint256)")
TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa"
    "952ba7f163c4a11628f55a4df523b3ef"
)

USDT_DECIMALS = 18
REQUIRED_CONFIRMATIONS = 12
POLL_INTERVAL_SECONDS = 10

# عند إعادة تشغيل البوت، يعيد فحص عدد من البلوكات السابقة.
INITIAL_LOOKBACK_BLOCKS = 5000
BLOCKS_PER_REQUEST = 500


class RpcError(RuntimeError):
    pass


def address_topic(address: str) -> str:
    """تحويل عنوان Ethereum/BSC إلى topic بطول 32 بايت."""
    clean_address = address.lower().removeprefix("0x")
    return "0x" + clean_address.rjust(64, "0")


def parse_sqlite_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


async def rpc_call(
    session: aiohttp.ClientSession,
    rpc_url: str,
    method: str,
    params: list[Any],
) -> Any:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

    async with session.post(
        rpc_url,
        json=payload,
    ) as response:
        response.raise_for_status()
        data = await response.json()

    if data.get("error"):
        raise RpcError(
            f"{method}: {data['error']}"
        )

    if "result" not in data:
        raise RpcError(
            f"رد غير صالح من RPC عند تنفيذ {method}"
        )

    return data["result"]


async def get_latest_block(
    session: aiohttp.ClientSession,
    rpc_url: str,
) -> int:
    result = await rpc_call(
        session,
        rpc_url,
        "eth_blockNumber",
        [],
    )
    return int(result, 16)


async def get_transfer_logs(
    session: aiohttp.ClientSession,
    rpc_url: str,
    wallet_address: str,
    from_block: int,
    to_block: int,
) -> list[dict[str, Any]]:
    if from_block > to_block:
        return []

    result = await rpc_call(
        session,
        rpc_url,
        "eth_getLogs",
        [
            {
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "address": USDT_CONTRACT,
                "topics": [
                    TRANSFER_TOPIC,
                    None,
                    address_topic(wallet_address),
                ],
            }
        ],
    )

    return list(result)


async def get_block_timestamp(
    session: aiohttp.ClientSession,
    rpc_url: str,
    block_number: int,
) -> datetime:
    block = await rpc_call(
        session,
        rpc_url,
        "eth_getBlockByNumber",
        [hex(block_number), False],
    )

    if not block or "timestamp" not in block:
        raise RpcError(
            f"تعذر قراءة البلوك رقم {block_number}"
        )

    timestamp = int(block["timestamp"], 16)

    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc,
    )


async def complete_paid_order(
    *,
    bot: Bot,
    db: Database,
    shop: ShopDigitalClient,
    settings: Settings,
    external_order_id: str,
) -> None:
    order = await db.get_order_by_external_id(
        external_order_id
    )

    if not order:
        logger.error(
            "Order not found after payment: %s",
            external_order_id,
        )
        return

    user_id = int(order["user_id"])
    product_id = str(order["product_id"])
    product_name = str(order["product_name"])

    try:
        await db.mark_order_processing(
            external_order_id
        )

        result = await shop.purchase(
            product_id=product_id,
            quantity=int(order.get("quantity", 1)),
            external_order_id=external_order_id,
        )

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

        await bot.send_message(
            user_id,
            "✅ <b>تم تأكيد الدفع وتنفيذ طلبك</b>\n\n"
            f"🛍 المنتج: {product_name}\n"
            f"🧾 رقم الطلب: "
            f"<code>{provider_order_id}</code>\n\n"
            "🔐 بيانات الاشتراك:\n"
            f"<code>{credentials}</code>",
        )

    except ShopDigitalError as exc:
        error_message = (
            f"تم استلام الدفع لكن فشل طلب المزود: {exc}"
        )

        await db.mark_order_failed(
            external_order_id,
            error_message,
        )

        await bot.send_message(
            user_id,
            "⚠️ تم تأكيد دفعتك، لكن تعذر تنفيذ "
            "المنتج تلقائيًا.\n"
            "تم إرسال الطلب إلى الإدارة للمراجعة.\n\n"
            f"🧾 رقم الطلب:\n"
            f"<code>{external_order_id}</code>",
        )

        if settings.admin_id:
            await bot.send_message(
                settings.admin_id,
                "⚠️ <b>دفعة مؤكدة وطلب فاشل</b>\n\n"
                f"المستخدم: <code>{user_id}</code>\n"
                f"الطلب: "
                f"<code>{external_order_id}</code>\n"
                f"المنتج: {product_name}\n"
                f"الخطأ: {exc}",
            )

    except Exception as exc:
        logger.exception(
            "Unexpected order completion error: %s",
            external_order_id,
        )

        await db.mark_order_failed(
            external_order_id,
            f"خطأ بعد استلام الدفع: {exc}",
        )

        await bot.send_message(
            user_id,
            "⚠️ تم تأكيد دفعتك، لكن حدث خطأ أثناء "
            "تجهيز المنتج.\n"
            "ستقوم الإدارة بمراجعة الطلب.\n\n"
            f"🧾 رقم الطلب:\n"
            f"<code>{external_order_id}</code>",
        )

        if settings.admin_id:
            await bot.send_message(
                settings.admin_id,
                "🚨 <b>خطأ بعد تأكيد دفعة</b>\n\n"
                f"المستخدم: <code>{user_id}</code>\n"
                f"الطلب: "
                f"<code>{external_order_id}</code>\n"
                f"الخطأ: <code>{exc}</code>",
            )


async def process_transfer_log(
    *,
    log: dict[str, Any],
    waiting_by_amount: dict[Decimal, dict[str, Any]],
    session: aiohttp.ClientSession,
    bot: Bot,
    db: Database,
    shop: ShopDigitalClient,
    settings: Settings,
) -> None:
    if log.get("removed"):
        return

    data = log.get("data")
    tx_hash = log.get("transactionHash")
    block_hex = log.get("blockNumber")

    if not data or not tx_hash or not block_hex:
        return

    raw_amount = int(data, 16)
    transfer_amount = (
        Decimal(raw_amount)
        / Decimal(10**USDT_DECIMALS)
    )

    payment = waiting_by_amount.get(transfer_amount)

    if not payment:
        return

    block_number = int(block_hex, 16)
    transfer_time = await get_block_timestamp(
        session,
        settings.bsc_rpc_url,
        block_number,
    )

    created_at = parse_sqlite_datetime(
        payment["created_at"]
    )
    expires_at = parse_sqlite_datetime(
        payment["expires_at"]
    )

    # يمنع قبول تحويل قديم له نفس المبلغ.
    if transfer_time < created_at:
        return

    if transfer_time > expires_at:
        return

    external_order_id = str(
        payment["external_order_id"]
    )

    marked = await db.mark_payment_paid(
        external_order_id,
        tx_hash,
    )

    if not marked:
        return

    logger.info(
        "USDT payment confirmed: order=%s tx=%s amount=%s",
        external_order_id,
        tx_hash,
        transfer_amount,
    )

    await bot.send_message(
        int(payment["user_id"]),
        "✅ <b>تم اكتشاف دفعتك بنجاح</b>\n"
        "⏳ جارٍ تجهيز المنتج الآن...",
    )

    await complete_paid_order(
        bot=bot,
        db=db,
        shop=shop,
        settings=settings,
        external_order_id=external_order_id,
    )


async def monitor_payments(
    *,
    bot: Bot,
    db: Database,
    shop: ShopDigitalClient,
    settings: Settings,
) -> None:
    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:
        latest = await get_latest_block(
            session,
            settings.bsc_rpc_url,
        )

        last_scanned_block = max(
            0,
            latest
            - REQUIRED_CONFIRMATIONS
            - INITIAL_LOOKBACK_BLOCKS,
        )

        logger.info(
            "USDT payment monitor started at block %s",
            last_scanned_block,
        )

        while True:
            try:
                await db.expire_old_payments()

                waiting = await db.get_waiting_payments(
                    limit=500
                )

                latest = await get_latest_block(
                    session,
                    settings.bsc_rpc_url,
                )

                confirmed_block = (
                    latest - REQUIRED_CONFIRMATIONS
                )

                if confirmed_block <= last_scanned_block:
                    await asyncio.sleep(
                        POLL_INTERVAL_SECONDS
                    )
                    continue

                if not waiting:
                    last_scanned_block = confirmed_block
                    await asyncio.sleep(
                        POLL_INTERVAL_SECONDS
                    )
                    continue

                waiting_by_amount = {
                    Decimal(str(item["payment_amount"])): item
                    for item in waiting
                }

                start_block = last_scanned_block + 1

                while start_block <= confirmed_block:
                    end_block = min(
                        start_block
                        + BLOCKS_PER_REQUEST
                        - 1,
                        confirmed_block,
                    )

                    logs = await get_transfer_logs(
                        session,
                        settings.bsc_rpc_url,
                        settings.usdt_wallet,
                        start_block,
                        end_block,
                    )

                    for event_log in logs:
                        await process_transfer_log(
                            log=event_log,
                            waiting_by_amount=(
                                waiting_by_amount
                            ),
                            session=session,
                            bot=bot,
                            db=db,
                            shop=shop,
                            settings=settings,
                        )

                    last_scanned_block = end_block
                    start_block = end_block + 1

                await asyncio.sleep(
                    POLL_INTERVAL_SECONDS
                )

            except asyncio.CancelledError:
                logger.info(
                    "USDT payment monitor stopped"
                )
                raise

            except Exception:
                logger.exception(
                    "USDT payment monitor error"
                )
                await asyncio.sleep(15)
