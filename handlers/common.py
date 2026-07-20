from decimal import Decimal, ROUND_HALF_UP


def calculate_sale_price(
    provider_price: str | int | float | Decimal,
    margin: Decimal,
) -> Decimal:
    price = Decimal(str(provider_price))
    sale = price * (Decimal("1") + margin)
    return sale.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def money(value: Decimal | str | float | int) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01')):.2f}"


def safe_text(value: object, fallback: str = "غير متوفر") -> str:
    text = str(value).strip() if value is not None else ""
    return text or fallback
