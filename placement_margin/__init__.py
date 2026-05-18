"""Placement Net Margin % calculator package.

Pure-function margin calculation for Bullhorn placements, plus the webhook
subscription, listener, worker, and admin UI that surround it.

The calculator (`placement_margin.calculator`) is intentionally
dependency-free so it can be unit-tested in isolation from Flask,
SQLAlchemy, and the Bullhorn REST client.
"""

from placement_margin.calculator import (
    calculate_net_margin_percent,
    MarginInputs,
    MarginResult,
    MarginStatus,
)

__all__ = [
    "calculate_net_margin_percent",
    "MarginInputs",
    "MarginResult",
    "MarginStatus",
]
