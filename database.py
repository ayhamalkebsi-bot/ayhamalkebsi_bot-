from __future__ import annotations

import aiosqlite
from decimal import Decimal
from typing import Any


PAYMENT_PRECISION = Decimal("0.001")


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    balance TEXT NOT NULL DEFAULT '0.00',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_order_id TEXT UNIQUE NOT NULL,
                    user_id INTEGER NOT NULL,
                    provider_order_id TEXT,
                    product_id TEXT NOT NULL,
                    product_name TEXT NOT NULL,
                    provider_price TEXT NOT NULL,
                    sale_price TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL,
                    credentials TEXT,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS usdt_payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_order_id TEXT UNIQUE NOT NULL,
                    user_id INTEGER NOT NULL,
                    wallet_address TEXT NOT NULL,
                    network TEXT NOT NULL DEFAULT 'BEP20',
                    base_amount TEXT NOT NULL,
                    payment_amount TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'waiting',
                    tx_hash TEXT,
                    error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    paid_at TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                    FOREIGN KEY (external_order_id)
                    REFERENCES orders(external_order_id)
                    ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_orders_user_id
                ON orders(user_id);

                CREATE INDEX IF NOT EXISTS idx_orders_status
                ON orders(status);

                CREATE INDEX IF NOT EXISTS idx_usdt_payments_user_id
                ON usdt_payments(user_id);

                CREATE INDEX IF NOT EXISTS idx_usdt_payments_status
                ON usdt_payments(status);

                CREATE INDEX IF NOT EXISTS idx_usdt_payments_amount
                ON usdt_payments(payment_amount);

                CREATE UNIQUE INDEX IF NOT EXISTS
                idx_unique_waiting_payment_amount
                ON usdt_payments(payment_amount)
                WHERE status = 'waiting';

                CREATE UNIQUE INDEX IF NOT EXISTS
                idx_unique_usdt_tx_hash
                ON usdt_payments(tx_hash)
                WHERE tx_hash IS NOT NULL;
                """
            )

            await db.commit()

    async def upsert_user(
        self,
        user_id: int,
        username: str | None,
        full_name: str,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO users (
                    user_id,
                    username,
                    full_name
                )
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name
                """,
                (
                    user_id,
                    username,
                    full_name,
                ),
            )
            await db.commit()

    async def get_user(
        self,
        user_id: int,
    ) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                """
                SELECT *
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_balance(
        self,
        user_id: int,
    ) -> Decimal:
        user = await self.get_user(user_id)

        if not user:
            return Decimal("0.00")

        return Decimal(user["balance"])

    async def set_balance(
        self,
        user_id: int,
        amount: Decimal,
    ) -> None:
        amount = amount.quantize(Decimal("0.01"))

        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE users
                SET balance = ?
                WHERE user_id = ?
                """,
                (
                    str(amount),
                    user_id,
                ),
            )
            await db.commit()

    async def change_balance(
        self,
        user_id: int,
        delta: Decimal,
    ) -> Decimal:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            await db.execute("BEGIN IMMEDIATE")

            async with db.execute(
                """
                SELECT balance
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                await db.rollback()
                raise ValueError("المستخدم غير موجود")

            current_balance = Decimal(row["balance"])
            new_balance = (
                current_balance + delta
            ).quantize(Decimal("0.01"))

            if new_balance < 0:
                await db.rollback()
                raise ValueError("الرصيد غير كافٍ")

            await db.execute(
                """
                UPDATE users
                SET balance = ?
                WHERE user_id = ?
                """,
                (
                    str(new_balance),
                    user_id,
                ),
            )

            await db.commit()
            return new_balance

    async def create_pending_order(
        self,
        *,
        external_order_id: str,
        user_id: int,
        product_id: str,
        product_name: str,
        provider_price: Decimal,
        sale_price: Decimal,
        quantity: int,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                INSERT INTO orders (
                    external_order_id,
                    user_id,
                    product_id,
                    product_name,
                    provider_price,
                    sale_price,
                    quantity,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_payment')
                """,
                (
                    external_order_id,
                    user_id,
                    product_id,
                    product_name,
                    str(provider_price),
                    str(sale_price),
                    quantity,
                ),
            )

            await db.commit()

    async def mark_order_processing(
        self,
        external_order_id: str,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE orders
                SET status = 'processing',
                    updated_at = CURRENT_TIMESTAMP
                WHERE external_order_id = ?
                """,
                (external_order_id,),
            )
            await db.commit()

    async def mark_order_success(
        self,
        external_order_id: str,
        provider_order_id: str,
        credentials: str,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE orders
                SET status = 'completed',
                    provider_order_id = ?,
                    credentials = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE external_order_id = ?
                """,
                (
                    provider_order_id,
                    credentials,
                    external_order_id,
                ),
            )
            await db.commit()

    async def mark_order_failed(
        self,
        external_order_id: str,
        error_message: str,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE orders
                SET status = 'failed',
                    error_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE external_order_id = ?
                """,
                (
                    error_message,
                    external_order_id,
                ),
            )
            await db.commit()

    async def mark_order_expired(
        self,
        external_order_id: str,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE orders
                SET status = 'expired',
                    error_message = 'انتهت مهلة الدفع',
                    updated_at = CURRENT_TIMESTAMP
                WHERE external_order_id = ?
                  AND status = 'pending_payment'
                """,
                (external_order_id,),
            )
            await db.commit()

    async def get_order_by_external_id(
        self,
        external_order_id: str,
    ) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                """
                SELECT *
                FROM orders
                WHERE external_order_id = ?
                """,
                (external_order_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_user_orders(
        self,
        user_id: int,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                """
                SELECT *
                FROM orders
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (
                    user_id,
                    limit,
                ),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def create_usdt_payment(
        self,
        *,
        external_order_id: str,
        user_id: int,
        wallet_address: str,
        base_amount: Decimal,
        expiration_minutes: int = 30,
    ) -> Decimal:
        if expiration_minutes < 1:
            raise ValueError(
                "مدة الدفع يجب أن تكون دقيقة واحدة على الأقل"
            )

        base_amount = base_amount.quantize(PAYMENT_PRECISION)

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")

            # تحرير المبالغ التابعة للمدفوعات التي انتهت مدتها.
            await db.execute(
                """
                UPDATE usdt_payments
                SET status = 'expired',
                    error_message = 'انتهت مهلة الدفع',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'waiting'
                  AND expires_at <= CURRENT_TIMESTAMP
                """
            )

            # إنشاء مبلغ فريد، مثل:
            # 5.001 ثم 5.002 ثم 5.003...
            selected_amount: Decimal | None = None

            for number in range(1, 1000):
                unique_part = Decimal(number) / Decimal("1000")

                candidate = (
                    base_amount + unique_part
                ).quantize(PAYMENT_PRECISION)

                async with db.execute(
                    """
                    SELECT id
                    FROM usdt_payments
                    WHERE payment_amount = ?
                      AND status = 'waiting'
                    LIMIT 1
                    """,
                    (str(candidate),),
                ) as cursor:
                    existing = await cursor.fetchone()

                if not existing:
                    selected_amount = candidate
                    break

            if selected_amount is None:
                await db.rollback()
                raise RuntimeError(
                    "تعذر إنشاء مبلغ دفع فريد. حاول لاحقًا."
                )

            await db.execute(
                """
                INSERT INTO usdt_payments (
                    external_order_id,
                    user_id,
                    wallet_address,
                    network,
                    base_amount,
                    payment_amount,
                    status,
                    expires_at
                )
                VALUES (
                    ?,
                    ?,
                    ?,
                    'BEP20',
                    ?,
                    ?,
                    'waiting',
                    datetime(
                        'now',
                        '+' || ? || ' minutes'
                    )
                )
                """,
                (
                    external_order_id,
                    user_id,
                    wallet_address,
                    str(base_amount),
                    str(selected_amount),
                    expiration_minutes,
                ),
            )

            await db.commit()
            return selected_amount

    async def get_payment_by_order_id(
        self,
        external_order_id: str,
    ) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                """
                SELECT *
                FROM usdt_payments
                WHERE external_order_id = ?
                """,
                (external_order_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_payment_by_amount(
        self,
        payment_amount: Decimal,
    ) -> dict[str, Any] | None:
        amount = payment_amount.quantize(PAYMENT_PRECISION)

        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                """
                SELECT *
                FROM usdt_payments
                WHERE payment_amount = ?
                  AND status = 'waiting'
                  AND expires_at > CURRENT_TIMESTAMP
                ORDER BY id ASC
                LIMIT 1
                """,
                (str(amount),),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_waiting_payments(
        self,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                """
                SELECT *
                FROM usdt_payments
                WHERE status = 'waiting'
                  AND expires_at > CURRENT_TIMESTAMP
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_user_waiting_payment(
        self,
        user_id: int,
    ) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                """
                SELECT *
                FROM usdt_payments
                WHERE user_id = ?
                  AND status = 'waiting'
                  AND expires_at > CURRENT_TIMESTAMP
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def mark_payment_paid(
        self,
        external_order_id: str,
        tx_hash: str,
    ) -> bool:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")

            try:
                cursor = await db.execute(
                    """
                    UPDATE usdt_payments
                    SET status = 'paid',
                        tx_hash = ?,
                        paid_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE external_order_id = ?
                      AND status = 'waiting'
                      AND expires_at > CURRENT_TIMESTAMP
                    """,
                    (
                        tx_hash,
                        external_order_id,
                    ),
                )
            except aiosqlite.IntegrityError:
                await db.rollback()
                return False

            changed = cursor.rowcount == 1

            if not changed:
                await db.rollback()
                return False

            await db.execute(
                """
                UPDATE orders
                SET status = 'paid',
                    updated_at = CURRENT_TIMESTAMP
                WHERE external_order_id = ?
                  AND status = 'pending_payment'
                """,
                (external_order_id,),
            )

            await db.commit()
            return True

    async def mark_payment_failed(
        self,
        external_order_id: str,
        error_message: str,
    ) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                UPDATE usdt_payments
                SET status = 'failed',
                    error_message = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE external_order_id = ?
                """,
                (
                    error_message,
                    external_order_id,
                ),
            )
            await db.commit()

    async def expire_old_payments(self) -> int:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")

            cursor = await db.execute(
                """
                UPDATE usdt_payments
                SET status = 'expired',
                    error_message = 'انتهت مهلة الدفع',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'waiting'
                  AND expires_at <= CURRENT_TIMESTAMP
                """
            )

            expired_count = cursor.rowcount

            await db.execute(
                """
                UPDATE orders
                SET status = 'expired',
                    error_message = 'انتهت مهلة الدفع',
                    updated_at = CURRENT_TIMESTAMP
                WHERE external_order_id IN (
                    SELECT external_order_id
                    FROM usdt_payments
                    WHERE status = 'expired'
                )
                  AND status = 'pending_payment'
                """
            )

            await db.commit()
            return expired_count

    async def stats(self) -> dict[str, Any]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                """
                SELECT COUNT(*) AS count
                FROM users
                """
            ) as cursor:
                users_count = (await cursor.fetchone())["count"]

            async with db.execute(
                """
                SELECT
                    COUNT(*) AS orders_count,
                    COALESCE(
                        SUM(CAST(sale_price AS REAL)),
                        0
                    ) AS revenue
                FROM orders
                WHERE status = 'completed'
                """
            ) as cursor:
                row = await cursor.fetchone()

            return {
                "users_count": users_count,
                "orders_count": row["orders_count"],
                "revenue": Decimal(
                    str(row["revenue"])
                ).quantize(Decimal("0.01")),
            }
