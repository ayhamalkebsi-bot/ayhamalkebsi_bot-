from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="🛍 المنتجات",
                callback_data="products",
            )
        ],
        [
            InlineKeyboardButton(
                text="💵 رصيدي",
                callback_data="balance",
            ),
            InlineKeyboardButton(
                text="📜 طلباتي",
                callback_data="my_orders",
            ),
        ],
    ]

    if is_admin:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⚙️ لوحة الإدارة",
                    callback_data="admin_panel",
                )
            ]
        )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def product_keyboard(
    product_id: str,
    sale_price: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"شراء بـ ${sale_price}",
                    callback_data=f"buy:{product_id}",
                )
            ]
        ]
    )


def confirm_purchase_keyboard(
    product_id: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ تأكيد الشراء",
                    callback_data=f"confirm_buy:{product_id}",
                ),
                InlineKeyboardButton(
                    text="❌ إلغاء",
                    callback_data="cancel_buy",
                ),
            ]
        ]
    )


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ إضافة رصيد",
                    callback_data="admin_add_balance",
                ),
                InlineKeyboardButton(
                    text="➖ خصم رصيد",
                    callback_data="admin_remove_balance",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📊 الإحصائيات",
                    callback_data="admin_stats",
                ),
                InlineKeyboardButton(
                    text="💰 رصيد المزود",
                    callback_data="provider_balance",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ رجوع",
                    callback_data="back_main",
                )
            ],
        ]
    )
