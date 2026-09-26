"""Money value object (§20 + review note).

Money is never a bare float. DB columns are NUMERIC(18,4) + ISO-4217 currency.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

VALID_CURRENCIES = {"EGP", "USD", "EUR", "GBP", "AED", "SAR", "KWD", "QAR", "OMR", "BHD", "JOD", "TRY"}

_QUANT = Decimal("0.0001")


class Money:
    """Immutable Money value object."""

    __slots__ = ("amount", "currency")

    def __init__(self, amount: Decimal | str | int, currency: str = "EGP") -> None:
        amt = Decimal(str(amount)).quantize(_QUANT, rounding=ROUND_HALF_UP)
        cur = currency.upper()
        if cur not in VALID_CURRENCIES:
            raise ValueError(f"Invalid currency: {currency}")
        self.amount = amt
        self.currency = cur

    def add(self, other: "Money") -> "Money":
        self._assertSameCurrency(other)
        return Money(self.amount + other.amount, self.currency)

    def sub(self, other: "Money") -> "Money":
        self._assertSameCurrency(other)
        return Money(self.amount - other.amount, self.currency)

    def mul(self, factor: Decimal | str | int | float) -> "Money":
        f = Decimal(str(factor))
        return Money(self.amount * f, self.currency)

    def pct(self, percentage: Decimal | str | int | float) -> "Money":
        return self.mul(Decimal(str(percentage)) / Decimal(100))

    def is_zero(self) -> bool:
        return self.amount == 0

    def is_negative(self) -> bool:
        return self.amount < 0

    def _assertSameCurrency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError(f"Currency mismatch: {self.currency} vs {other.currency}")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Money) and self.amount == other.amount and self.currency == other.currency

    def __repr__(self) -> str:
        return f"Money({self.amount}, {self.currency})"

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"

    # --- serialization ---
    def to_dict(self) -> dict[str, Any]:
        return {"amount": str(self.amount), "currency": self.currency}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Money":
        return cls(data["amount"], data.get("currency", "EGP"))

    # --- pydantic integration ---
    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            cls._validate,
            serialization=core_schema.plain_serializer_function_ser_schema(cls.to_dict),
        )

    @classmethod
    def _validate(cls, value: Any) -> "Money":
        if isinstance(value, Money):
            return value
        if isinstance(value, dict):
            return cls.from_dict(value)
        if isinstance(value, (str, int, Decimal)):
            return cls(value)
        raise ValueError(f"Cannot parse Money from {value!r}")
