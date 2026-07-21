from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import aiohttp
from aiogram import Bot

from config import Settings
from database import Database
from handlers.common import safe_text
from shopdigital import ShopDigitalClient, ShopDigitalError


logger = logging.getLogger(__name__)

# عقد USDT على شبكة BNB Smart Chain.
USDT_CONTRACT = (
    "0x55d398326f99059ff775485246999027b3197955"
)

# keccak256("Transfer(address,address,uint256)")
TRANSFER_TOPIC = (
    "0xddf252ad1be2c89b69c2b068fc378daa"
    "952ba7f163c4a11628f55a4df523b3ef"
)

USDT_DECIMALS = 18

# عدد التأكيدات قبل اعتماد التحويل.
REQUIRED_CONFIRMATIONS = 12

# مدة الانتظار بين كل فحص.
POLL_INTERVAL_SECONDS = 15

# يغطي الدفعات السابقة بعد إعادة تشغيل البوت.
# شبكة BSC تنتج بلوكًا كل عدة ثوانٍ تقريبًا،
# لذلك 1000 بلوك يغطي أكثر من مهلة الدفع غالبًا.
INITIAL_LOOKBACK_BLOCKS = 1000

# نطاق صغير لتقليل احتمالية رفض eth_getLogs.
BLOCKS_PER_REQUEST = 25

# عدد محاولات طلبات RPC المؤقتة.
RPC_MAX_RETRIES = 3


class RpcError(RuntimeError):
    """خطأ صادر من JSON-RPC."""


class RpcHttpError(RpcError):
    """خطأ HTTP صادر من مزود RPC."""

    def __init__(
        self,
        *,
        status: int,
        method: str,
        message: str,
    ) -> None:
        self.status = status
        self.method = method
        self.message = message

        super().__init__(
            f"{method} HTTP {status}: {message}"
        )


def address_topic(address: str) -> str:
    """تحويل عنوان BSC إلى topic بطول 32 بايت."""
    clean_address = address.strip().lower()

    if clean_address.startswith("0x"):
        clean_address = clean_address[2:]

    if len(clean_address) != 40:
        raise ValueError(
            f"عنوان المحفظة غير صالح: {address}"
        )

    return "0x" + clean_address.rjust(64, "0")


def parse_sqlite_datetime(value: str) -> datetime:
    """تحويل وقت SQLite إلى UTC."""
    parsed = datetime.fromisoformat(value)

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed.astimezone(timezone.utc)


async def rpc_call(
    session: aiohttp.ClientSession,
    rpc_url: str,
    method: str,
    params: list[Any],
) -> Any:
    """تنفيذ طلب JSON-RPC مع إعادة المحاولة للأخطاء المؤقتة."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }

    last_error: Exception | None = None

    for attempt in range(1, RPC_MAX_RETRIES + 1):
        try:
            async with session.post(
                rpc_url,
                json=payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            ) as response:
                response_text = await response.text()

                if response.status != 200:
                    error = RpcHttpError(
                        status=response.status,
                        method=method,
                        message=response_text[:500],
                    )

                    # 403 يُعالَج لاحقًا بتقسيم نطاق البلوكات.
                    if response.status == 403:
                        raise error

                    # إعادة المحاولة للأخطاء المؤقتة.
                    if (
                        response.status == 429
                        or response.status >= 500
                    ):
                        last_error = error

                        retry_after = (
                            response.headers.get(
                                "Retry-After"
                            )
                        )

                        if retry_after:
                            try:
                                delay = float(
                                    retry_after
                                )
                            except ValueError:
                                delay = attempt * 2
                        else:
                            delay = attempt * 2

                        logger.warning(
                            "RPC temporary HTTP error: "
                            "method=%s status=%s "
                            "attempt=%s/%s retry_in=%ss",
                            method,
                            response.status,
                            attempt,
                            RPC_MAX_RETRIES,
                            delay,
                        )

                        await asyncio.sleep(delay)
                        continue

                    raise error

                try:
                    data = json.loads(response_text)
                except json.JSONDecodeError as exc:
                    raise RpcError(
                        f"{method}: رد JSON غير صالح: "
                        f"{response_text[:300]}"
                    ) from exc

                if data.get("error"):
                    raise RpcError(
                        f"{method}: {data['error']}"
                    )

                if "result" not in data:
                    raise RpcError(
                        "رد غير صالح من RPC عند تنفيذ "
                        f"{method}: {data}"
                    )

                return data["result"]

        except (
            aiohttp.ClientConnectionError,
            aiohttp.ServerTimeoutError,
            asyncio.TimeoutError,
        ) as exc:
            last_error = exc

            if attempt >= RPC_MAX_RETRIES:
                break

            delay = attempt * 2

            logger.warning(
                "RPC connection error: "
                "method=%s attempt=%s/%s "
                "retry_in=%ss error=%s",
                method,
                attempt,
                RPC_MAX_RETRIES,
                delay,
                exc,
            )

            await asyncio.sleep(delay)

    raise RpcError(
        f"فشل الاتصال بـ RPC عند تنفيذ {method}: "
        f"{last_error}"
    )


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
    """
    قراءة تحويلات USDT إلى المحفظة.

    إذا رفض Chainstack نطاق البلوكات بـ403،
    يُقسَّم النطاق تلقائيًا إلى أجزاء أصغر.
    """
    if from_block > to_block:
        return []

    params = [
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
    ]

    try:
        result = await rpc_call(
            session,
            rpc_url,
            "eth_getLogs",
            params,
        )

        return list(result)

    except RpcHttpError as exc:
        block_count = (
            to_block - from_block + 1
        )

        # بعض مزودي RPC يرفضون نطاقًا كبيرًا.
        # نقسم النطاق إلى نصفين تلقائيًا.
        if exc.status in {403, 413} and block_count > 1:
            middle_block = (
                from_block + to_block
            ) // 2

            logger.warning(
                "eth_getLogs rejected for blocks "
                "%s-%s with HTTP %s; "
                "splitting request",
                from_block,
                to_block,
                exc.status,
            )

            first_half = await get_transfer_logs(
                session,
                rpc_url,
                wallet_address,
                from_block,
                middle_block,
            )

            second_half = await get_transfer_logs(
                session,
                rpc_url,
                wallet_address,
                middle_block + 1,
                to_block,
            )

            return first_half + second_half

        # إذا كان حتى البلوك الواحد مرفوضًا،
        # فالمشكلة من صلاحية العقدة أو إعداداتها.
        raise RpcError(
            "Chainstack رفض eth_getLogs حتى بعد "
            "تقليل نطاق البلوكات. "
            f"HTTP {exc.status}: {exc.message}"
        ) from exc


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

    timestamp = int(
        block["timestamp"],
        16,
    )

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
    quantity = int(order.get("quantity", 1))

    try:
        await db.mark_order_processing(
            external_order_id
        )

        result = await shop.purchase(
            product_id=product_id,
            quantity=quantity,
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
            "🧾 رقم الطلب: "
            f"<code>{provider_order_id}</code>\n\n"
            "🔐 بيانات الاشتراك:\n"
            f"<code>{credentials}</code>",
        )

    except ShopDigitalError as exc:
        error_message = (
            "تم استلام الدفع لكن فشل طلب المزود: "
            f"{exc}"
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
            "🧾 رقم الطلب:\n"
            f"<code>{external_order_id}</code>",
        )

        if settings.admin_id:
            await bot.send_message(
                settings.admin_id,
                "⚠️ <b>دفعة مؤكدة وطلب فاشل</b>\n\n"
                f"المستخدم: <code>{user_id}</code>\n"
                "الطلب: "
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
            "🧾 رقم الطلب:\n"
            f"<code>{external_order_id}</code>",
        )

        if settings.admin_id:
            await bot.send_message(
                settings.admin_id,
                "🚨 <b>خطأ بعد تأكيد دفعة</b>\n\n"
                f"المستخدم: <code>{user_id}</code>\n"
                "الطلب: "
                f"<code>{external_order_id}</code>\n"
                f"الخطأ: <code>{exc}</code>",
            )


async def process_transfer_log(
    *,
    log: dict[str, Any],
    waiting_by_amount: dict[
        Decimal,
        dict[str, Any],
    ],
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

    try:
        raw_amount = int(data, 16)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid USDT transfer data: %s",
            data,
        )
        return

    transfer_amount = (
        Decimal(raw_amount)
        / Decimal(10**USDT_DECIMALS)
    )

    payment = waiting_by_amount.get(
        transfer_amount
    )

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

    # منع قبول تحويل قديم يحمل نفس المبلغ.
    if transfer_time < created_at:
        logger.info(
            "Ignoring old transfer: tx=%s amount=%s",
            tx_hash,
            transfer_amount,
        )
        return

    # منع قبول تحويل حصل بعد انتهاء الطلب.
    if transfer_time > expires_at:
        logger.info(
            "Ignoring expired transfer: "
            "tx=%s amount=%s",
            tx_hash,
            transfer_amount,
        )
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
        "USDT payment confirmed: "
        "order=%s tx=%s amount=%s",
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
    timeout = aiohttp.ClientTimeout(
        total=45,
        connect=15,
        sock_read=30,
    )

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:
        last_scanned_block: int | None = None

        while True:
            try:
                latest = await get_latest_block(
                    session,
                    settings.bsc_rpc_url,
                )

                confirmed_block = max(
                    0,
                    latest - REQUIRED_CONFIRMATIONS,
                )

                # تهيئة المراقب لأول مرة أو بعد إعادة التشغيل.
                if last_scanned_block is None:
                    last_scanned_block = max(
                        0,
                        confirmed_block
                        - INITIAL_LOOKBACK_BLOCKS,
                    )

                    logger.info(
                        "USDT payment monitor started "
                        "at block %s; latest confirmed=%s",
                        last_scanned_block,
                        confirmed_block,
                    )

                await db.expire_old_payments()

                waiting = (
                    await db.get_waiting_payments(
                        limit=500
                    )
                )

                if (
                    confirmed_block
                    <= last_scanned_block
                ):
                    await asyncio.sleep(
                        POLL_INTERVAL_SECONDS
                    )
                    continue

                # لا داعي لاستدعاء eth_getLogs
                # عندما لا توجد دفعات معلقة.
                if not waiting:
                    last_scanned_block = (
                        confirmed_block
                    )

                    await asyncio.sleep(
                        POLL_INTERVAL_SECONDS
                    )
                    continue

                waiting_by_amount = {
                    Decimal(
                        str(item["payment_amount"])
                    ): item
                    for item in waiting
                }

                start_block = (
                    last_scanned_block + 1
                )

                while (
                    start_block
                    <= confirmed_block
                ):
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

                    logger.debug(
                        "Scanned USDT logs: "
                        "blocks=%s-%s logs=%s",
                        start_block,
                        end_block,
                        len(logs),
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

                    # لا يتم تحديث المؤشر إلا بعد نجاح
                    # قراءة النطاق بالكامل.
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

            except RpcHttpError as exc:
                logger.error(
                    "RPC HTTP error: "
                    "method=%s status=%s body=%s",
                    exc.method,
                    exc.status,
                    exc.message,
                )

                await asyncio.sleep(30)

            except RpcError as exc:
                logger.error(
                    "USDT RPC error: %s",
                    exc,
                )

                await asyncio.sleep(30)

            except Exception:
                logger.exception(
                    "USDT payment monitor error"
                )

                await asyncio.sleep(30)
