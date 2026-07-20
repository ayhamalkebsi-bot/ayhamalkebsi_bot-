from __future__ import annotations

import aiosqlite
from decimal import Decimal
from typing import Any


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(
                """
                PRAGMA journal_mode=WAL;

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

                CREATE INDEX IF NOT EXISTS idx_orders_user_id
                ON orders(user_id);

                CREATE INDEX IF NOT EXISTS idx_orders_status
                ON orders(status);
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
                INSERT INTO users (user_id, username, full_name)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name
                """,
                (user_id, username, full_name),
            )
            await db.commit()

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM users WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_balance(self, user_id: int) -> Decimal:
        user = await self.get_user(user_id)
        if not user:
            return Decimal("0.00")
        return Decimal(user["balance"])

    async def set_balance(self, user_id: int, amount: Decimal) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "UPDATE users SET balance = ? WHERE user_id = ?",
                (str(amount.quantize(Decimal("0.01"))), user_id),
            )
            await db.commit()

    async def change_balance(
        self,
        user_id: int,
        delta: Decimal,
    ) -> Decimal:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("BEGIN IMMEDIATE")

            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT balance FROM users WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if not row:
                await db.rollback()
                raise ValueError("المستخدم غير موجود")

            current = Decimal(row["balance"])
            new_balance = (current + delta).quantize(Decimal("0.01"))

            if new_balance < 0:
                await db.rollback()
                raise ValueError("الرصيد غير كافٍ")

            await db.execute(
                "UPDATE users SET balance = ? WHERE user_id = ?",
                (str(new_balance), user_id),
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
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
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
                (provider_order_id, credentials, external_order_id),
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
                (error_message, external_order_id),
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
                SELECT * FROM orders
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
                SELECT * FROM orders
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def stats(self) -> dict[str, Any]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                "SELECT COUNT(*) AS count FROM users"
            ) as cursor:
                users_count = (await cursor.fetchone())["count"]

            async with db.execute(
                """
                SELECT
                    COUNT(*) AS orders_count,
                    COALESCE(SUM(CAST(sale_price AS REAL)), 0) AS revenue
                FROM orders
                WHERE status = 'completed'
                """
            ) as cursor:
                row = await cursor.fetchone()

            return {
                "users_count": users_count,
                "orders_count": row["orders_count"],
                "revenue": Decimal(str(row["revenue"])).quantize(
                    Decimal("0.01")
                ),
            }
