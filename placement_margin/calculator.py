"""Pure Net Margin % calculator for Bullhorn placements.

Formula
-------
    Net Margin % = ((clientBillRate - (payRate + customBillRate1 + customBillRate2))
                    / clientBillRate) * 100

All inputs are hourly USD amounts pulled directly from the Bullhorn
Placement record:

    clientBillRate    -> native Bullhorn field, Hourly Bill Rate ($/hr)
    payRate           -> native Bullhorn field, Hourly Pay Rate ($/hr)
    customBillRate1   -> custom Bullhorn field, Hourly Burden ($/hr)
    customBillRate2   -> custom Bullhorn field, Other Costs Hourly ($/hr)

Output is stored as a numeric decimal value (e.g. 28.5 -> represents 28.5%).
The "%" symbol lives in the Bullhorn field label, not in the stored value.

Edge cases
----------
    clientBillRate is None or 0   -> MarginResult(value=None,
                                                  status=MISSING_BILL_RATE
                                                  or ZERO_BILL_RATE).
    payRate is None               -> MarginResult(value=None,
                                                  status=MISSING_PAY_RATE).
    customBillRate1 is None       -> treated as 0 (burden absent).
    customBillRate2 is None       -> treated as 0 (other costs absent).
    Negative result               -> returned as-is so Finance can see
                                     the red flag.

This module has zero Flask / SQLAlchemy / Bullhorn dependencies — it is
trivially testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from enum import Enum
from typing import Any, Optional


class MarginStatus(str, Enum):
    """Outcome category for a calculation attempt."""

    OK = "ok"
    MISSING_BILL_RATE = "missing_bill_rate"
    ZERO_BILL_RATE = "zero_bill_rate"
    MISSING_PAY_RATE = "missing_pay_rate"
    INVALID_INPUT = "invalid_input"


@dataclass(frozen=True)
class MarginInputs:
    """Snapshot of the four Bullhorn input fields used in the formula."""

    client_bill_rate: Optional[float]
    pay_rate: Optional[float]
    custom_bill_rate_1: Optional[float]  # Hourly Burden $/hr
    custom_bill_rate_2: Optional[float]  # Other Costs Hourly $/hr


@dataclass(frozen=True)
class MarginResult:
    """Outcome of a calculation, with full traceability."""

    value: Optional[float]            # The Net Margin %, e.g. 28.5 (None if not computable)
    status: MarginStatus              # Categorical reason if value is None, OK otherwise
    inputs: MarginInputs              # Echo of the inputs used, for audit logging
    detail: Optional[str] = None      # Human-readable explanation for INVALID_INPUT


# Output is rounded to 2 decimal places for storage in customFloat1.
# Bullhorn float fields preserve up to ~15 significant digits, but Finance
# only displays/reports at 2dp, and over-precision invites false-precision
# disputes ("why does 28.53847% not match my spreadsheet's 28.54%?").
_QUANTIZE = Decimal("0.01")


def _coerce_to_decimal(raw: Any) -> Optional[Decimal]:
    """Best-effort coercion to Decimal. Returns None if value is None.

    Raises InvalidOperation on non-numeric, non-None input (caller catches).
    """
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw
    # str() conversion preserves precision better than float() for floats
    # like 13.1 that have unsurprising decimal representations but messy
    # binary ones.
    return Decimal(str(raw))


def calculate_net_margin_percent(inputs: MarginInputs) -> MarginResult:
    """Compute Net Margin % from a snapshot of placement input fields.

    Args:
        inputs: MarginInputs dataclass holding the four hourly rates.

    Returns:
        MarginResult with `value` populated (when status == OK) and `status`
        set to a categorical outcome. Always returns a result; never raises
        for normal None/zero/missing inputs.
    """
    # Type-coerce all four inputs once, up front. Any non-numeric value
    # short-circuits to INVALID_INPUT — defensive against malformed
    # Bullhorn payloads (e.g. clientBillRate sent as "N/A" string).
    try:
        bill = _coerce_to_decimal(inputs.client_bill_rate)
        pay = _coerce_to_decimal(inputs.pay_rate)
        burden = _coerce_to_decimal(inputs.custom_bill_rate_1)
        other = _coerce_to_decimal(inputs.custom_bill_rate_2)
    except (InvalidOperation, ValueError, TypeError) as exc:
        return MarginResult(
            value=None,
            status=MarginStatus.INVALID_INPUT,
            inputs=inputs,
            detail=f"Could not coerce input to numeric: {exc}",
        )

    # Bill rate gates everything — it's the denominator. Distinguish
    # missing (None) from explicit zero so the audit log captures intent.
    if bill is None:
        return MarginResult(
            value=None,
            status=MarginStatus.MISSING_BILL_RATE,
            inputs=inputs,
        )
    if bill == 0:
        return MarginResult(
            value=None,
            status=MarginStatus.ZERO_BILL_RATE,
            inputs=inputs,
        )

    # Pay rate is also mandatory — a placement with no pay rate is either
    # incomplete data entry or a salary placement (out of scope v1).
    # Either way, we can't compute margin meaningfully.
    if pay is None:
        return MarginResult(
            value=None,
            status=MarginStatus.MISSING_PAY_RATE,
            inputs=inputs,
        )

    # Burden and other costs default to 0 when absent — common on
    # placements that haven't had those fields filled in yet.
    burden_or_zero = burden if burden is not None else Decimal("0")
    other_or_zero = other if other is not None else Decimal("0")

    # Core formula.
    margin_decimal = (
        (bill - (pay + burden_or_zero + other_or_zero)) / bill
    ) * Decimal("100")

    # Round to 2dp using banker's-rounding alternative (ROUND_HALF_UP
    # matches finance/spreadsheet expectations).
    margin_rounded = margin_decimal.quantize(_QUANTIZE, rounding=ROUND_HALF_UP)

    return MarginResult(
        value=float(margin_rounded),
        status=MarginStatus.OK,
        inputs=inputs,
    )
