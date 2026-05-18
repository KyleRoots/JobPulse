"""Unit tests for placement_margin.calculator (T001).

Pure-function tests — no Flask app, no DB, no Bullhorn client.
"""

from decimal import Decimal

import pytest

from placement_margin.calculator import (
    MarginInputs,
    MarginStatus,
    calculate_net_margin_percent,
)


# ── Happy path ────────────────────────────────────────────────────────────────


def test_happy_path_typical_placement():
    """Bill $100, Pay $60, Burden $10, Other $5 → 25.00%."""
    result = calculate_net_margin_percent(
        MarginInputs(
            client_bill_rate=100.0,
            pay_rate=60.0,
            custom_bill_rate_1=10.0,
            custom_bill_rate_2=5.0,
        )
    )
    assert result.status == MarginStatus.OK
    assert result.value == 25.00


def test_happy_path_with_decimals():
    """Bill $87.50, Pay $52.00, Burden $7.50, Other $2.00 → 29.71%."""
    result = calculate_net_margin_percent(
        MarginInputs(
            client_bill_rate=87.50,
            pay_rate=52.00,
            custom_bill_rate_1=7.50,
            custom_bill_rate_2=2.00,
        )
    )
    assert result.status == MarginStatus.OK
    # (87.50 - (52.00 + 7.50 + 2.00)) / 87.50 * 100 = 26/87.50 * 100 = 29.714...%
    assert result.value == pytest.approx(29.71, abs=0.01)


def test_decimal_inputs_preserve_precision():
    """Decimal inputs are honored at full precision (str-coerce path)."""
    result = calculate_net_margin_percent(
        MarginInputs(
            client_bill_rate=Decimal("100.00"),
            pay_rate=Decimal("60.00"),
            custom_bill_rate_1=Decimal("10.00"),
            custom_bill_rate_2=Decimal("5.00"),
        )
    )
    assert result.status == MarginStatus.OK
    assert result.value == 25.00


# ── Missing burden / other costs default to zero ──────────────────────────────


def test_missing_burden_treated_as_zero():
    """customBillRate1 None → treated as 0."""
    result = calculate_net_margin_percent(
        MarginInputs(
            client_bill_rate=100.0,
            pay_rate=60.0,
            custom_bill_rate_1=None,
            custom_bill_rate_2=5.0,
        )
    )
    assert result.status == MarginStatus.OK
    # (100 - (60 + 0 + 5)) / 100 * 100 = 35.00%
    assert result.value == 35.00


def test_missing_other_costs_treated_as_zero():
    """customBillRate2 None → treated as 0."""
    result = calculate_net_margin_percent(
        MarginInputs(
            client_bill_rate=100.0,
            pay_rate=60.0,
            custom_bill_rate_1=10.0,
            custom_bill_rate_2=None,
        )
    )
    assert result.status == MarginStatus.OK
    # (100 - (60 + 10 + 0)) / 100 * 100 = 30.00%
    assert result.value == 30.00


def test_both_burden_and_other_missing_treated_as_zero():
    """Both custom fields None → simple bill-minus-pay margin."""
    result = calculate_net_margin_percent(
        MarginInputs(
            client_bill_rate=100.0,
            pay_rate=70.0,
            custom_bill_rate_1=None,
            custom_bill_rate_2=None,
        )
    )
    assert result.status == MarginStatus.OK
    # (100 - 70) / 100 * 100 = 30.00%
    assert result.value == 30.00


def test_explicit_zero_burden_same_as_missing():
    """customBillRate1 = 0 → equivalent to None for the math."""
    result = calculate_net_margin_percent(
        MarginInputs(
            client_bill_rate=100.0,
            pay_rate=60.0,
            custom_bill_rate_1=0.0,
            custom_bill_rate_2=5.0,
        )
    )
    assert result.status == MarginStatus.OK
    assert result.value == 35.00


# ── Bill rate edge cases (denominator) ────────────────────────────────────────


def test_missing_bill_rate_returns_null():
    """clientBillRate None → MISSING_BILL_RATE, no computation."""
    result = calculate_net_margin_percent(
        MarginInputs(
            client_bill_rate=None,
            pay_rate=60.0,
            custom_bill_rate_1=10.0,
            custom_bill_rate_2=5.0,
        )
    )
    assert result.status == MarginStatus.MISSING_BILL_RATE
    assert result.value is None


def test_zero_bill_rate_returns_null():
    """clientBillRate 0 → ZERO_BILL_RATE, no divide-by-zero."""
    result = calculate_net_margin_percent(
        MarginInputs(
            client_bill_rate=0.0,
            pay_rate=60.0,
            custom_bill_rate_1=10.0,
            custom_bill_rate_2=5.0,
        )
    )
    assert result.status == MarginStatus.ZERO_BILL_RATE
    assert result.value is None


def test_zero_bill_rate_distinguished_from_missing():
    """ZERO_BILL_RATE and MISSING_BILL_RATE are different statuses."""
    missing = calculate_net_margin_percent(
        MarginInputs(None, 60.0, 10.0, 5.0)
    )
    zero = calculate_net_margin_percent(
        MarginInputs(0.0, 60.0, 10.0, 5.0)
    )
    assert missing.status != zero.status
    assert missing.value is None
    assert zero.value is None


# ── Pay rate edge cases ───────────────────────────────────────────────────────


def test_missing_pay_rate_returns_null():
    """payRate None → MISSING_PAY_RATE, no computation."""
    result = calculate_net_margin_percent(
        MarginInputs(
            client_bill_rate=100.0,
            pay_rate=None,
            custom_bill_rate_1=10.0,
            custom_bill_rate_2=5.0,
        )
    )
    assert result.status == MarginStatus.MISSING_PAY_RATE
    assert result.value is None


def test_zero_pay_rate_computes_normally():
    """payRate 0 is a legitimate value (no labor cost) → compute normally."""
    result = calculate_net_margin_percent(
        MarginInputs(
            client_bill_rate=100.0,
            pay_rate=0.0,
            custom_bill_rate_1=10.0,
            custom_bill_rate_2=5.0,
        )
    )
    assert result.status == MarginStatus.OK
    # (100 - (0 + 10 + 5)) / 100 * 100 = 85.00%
    assert result.value == 85.00


# ── Negative margin (red flag — should still be stored) ───────────────────────


def test_negative_margin_returned_as_is():
    """Pay > Bill → negative margin, returned for Finance to see."""
    result = calculate_net_margin_percent(
        MarginInputs(
            client_bill_rate=50.0,
            pay_rate=60.0,
            custom_bill_rate_1=10.0,
            custom_bill_rate_2=5.0,
        )
    )
    assert result.status == MarginStatus.OK
    # (50 - 75) / 50 * 100 = -50.00%
    assert result.value == -50.00


def test_barely_negative_margin():
    """One-cent loss is still a loss — must surface."""
    result = calculate_net_margin_percent(
        MarginInputs(
            client_bill_rate=100.0,
            pay_rate=100.01,
            custom_bill_rate_1=None,
            custom_bill_rate_2=None,
        )
    )
    assert result.status == MarginStatus.OK
    assert result.value < 0
    assert result.value == pytest.approx(-0.01, abs=0.001)


# ── Invalid / malformed inputs ────────────────────────────────────────────────


def test_string_input_for_bill_rate_returns_invalid():
    """Non-numeric string → INVALID_INPUT with detail."""
    result = calculate_net_margin_percent(
        MarginInputs(
            client_bill_rate="N/A",
            pay_rate=60.0,
            custom_bill_rate_1=10.0,
            custom_bill_rate_2=5.0,
        )
    )
    assert result.status == MarginStatus.INVALID_INPUT
    assert result.value is None
    assert result.detail is not None


def test_numeric_string_input_accepted():
    """String '100.00' (e.g. from JSON-decoded Bullhorn payload) is OK."""
    result = calculate_net_margin_percent(
        MarginInputs(
            client_bill_rate="100.00",
            pay_rate="60.00",
            custom_bill_rate_1="10.00",
            custom_bill_rate_2="5.00",
        )
    )
    assert result.status == MarginStatus.OK
    assert result.value == 25.00


# ── Precision / rounding ──────────────────────────────────────────────────────


def test_rounding_to_two_decimals():
    """Long-tail decimal margin rounds to 2dp using ROUND_HALF_UP."""
    # Bill 30.00, Pay 20.00, Burden 0, Other 0 → 33.33333...% → 33.33
    result = calculate_net_margin_percent(
        MarginInputs(30.0, 20.0, 0.0, 0.0)
    )
    assert result.status == MarginStatus.OK
    assert result.value == 33.33


def test_rounding_half_up_at_boundary():
    """Half-up rounding: 25.005 → 25.01 (not banker's 25.00)."""
    # Construct inputs that produce exactly *.005 at 3dp.
    # Bill 200, Pay 149.99 → (200 - 149.99) / 200 * 100 = 25.005
    result = calculate_net_margin_percent(
        MarginInputs(200.0, 149.99, 0.0, 0.0)
    )
    assert result.status == MarginStatus.OK
    assert result.value == 25.01


# ── Input echo (for audit trail) ──────────────────────────────────────────────


def test_inputs_echoed_in_result_for_audit():
    """The MarginResult preserves a copy of the inputs for the audit log."""
    inputs = MarginInputs(100.0, 60.0, 10.0, 5.0)
    result = calculate_net_margin_percent(inputs)
    assert result.inputs == inputs
    assert result.inputs.client_bill_rate == 100.0
    assert result.inputs.pay_rate == 60.0


def test_inputs_echoed_even_on_failure():
    """Even when the calc bails, inputs must be in the result for forensics."""
    inputs = MarginInputs(None, 60.0, 10.0, 5.0)
    result = calculate_net_margin_percent(inputs)
    assert result.status == MarginStatus.MISSING_BILL_RATE
    assert result.inputs == inputs
