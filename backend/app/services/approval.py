from decimal import Decimal
from uuid import UUID


class PurchaseService:

    def validate_request(self, payload: dict) -> None:
        items = payload.get("items")

        if not items:
            raise ValueError("Purchase request must contain at least one item")

        for index, item in enumerate(items):
            if not item.get("item"):
                raise ValueError(f"Item {index} is missing 'item'")

            quantity = item.get("quantity")
            unit_price = item.get("unit_price")

            if quantity is None or quantity <= 0:
                raise ValueError(
                    f"Item {index} must have quantity greater than zero"
                )

            if unit_price is None or unit_price < 0:
                raise ValueError(
                    f"Item {index} must have a valid unit_price"
                )

    def calculate_total(self, payload: dict) -> Decimal:
        total = Decimal("0")

        for item in payload["items"]:
            quantity = Decimal(str(item["quantity"]))
            unit_price = Decimal(str(item["unit_price"]))

            total += quantity * unit_price

        return total

    def budget_exceeded(self, payload: dict, total: Decimal) -> bool:
        budget_limit = payload.get("budget_limit")

        if budget_limit is None:
            return False

        return total > Decimal(str(budget_limit))