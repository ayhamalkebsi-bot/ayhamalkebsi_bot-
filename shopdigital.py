from __future__ import annotations

from typing import Any
import aiohttp


class ShopDigitalError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ShopDigitalClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[Any]:
        timeout = aiohttp.ClientTimeout(total=30)

        try:
            async with aiohttp.ClientSession(
                headers=self.headers,
                timeout=timeout,
            ) as session:
                async with session.request(
                    method,
                    f"{self.base_url}{endpoint}",
                    json=json_data,
                ) as response:
                    try:
                        payload = await response.json()
                    except Exception:
                        payload = {"message": await response.text()}

                    if response.status >= 400:
                        messages = {
                            401: "مفتاح API غير صحيح أو تم إلغاؤه",
                            402: "رصيد المزود غير كافٍ",
                            404: "المنتج غير موجود",
                            409: "المنتج غير متوفر في المخزون",
                        }
                        message = messages.get(
                            response.status,
                            payload.get(
                                "message",
                                f"خطأ من المزود: HTTP {response.status}",
                            )
                            if isinstance(payload, dict)
                            else f"خطأ من المزود: HTTP {response.status}",
                        )
                        raise ShopDigitalError(message, response.status)

                    return payload

        except aiohttp.ClientError as exc:
            raise ShopDigitalError(
                f"تعذر الاتصال بخادم ShopDigital: {exc}"
            ) from exc

    async def products(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/api/products")

        if isinstance(payload, list):
            return payload

        products = payload.get("products", [])
        return products if isinstance(products, list) else []

    async def balance(self) -> dict[str, Any]:
        payload = await self._request("GET", "/api/balance")
        if not isinstance(payload, dict):
            raise ShopDigitalError("صيغة استجابة الرصيد غير متوقعة")
        return payload

    async def orders(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/api/orders")
        if isinstance(payload, list):
            return payload
        orders = payload.get("orders", [])
        return orders if isinstance(orders, list) else []

    async def purchase(
        self,
        *,
        product_id: str,
        quantity: int,
        external_order_id: str,
    ) -> dict[str, Any]:
        payload = await self._request(
            "POST",
            "/api/purchase",
            json_data={
                "product_id": product_id,
                "quantity": quantity,
                "external_order_id": external_order_id,
            },
        )
        if not isinstance(payload, dict):
            raise ShopDigitalError("صيغة استجابة الشراء غير متوقعة")
        return payload
