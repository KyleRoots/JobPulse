"""OpenAI cost telemetry + model-tier override helper.

Two public functions:
    resolve_model(site_id, default_model) -> str
        Returns `os.environ['MODEL_TIER_OVERRIDE_<SITE_ID>']` when set
        (uppercased, dots → underscores), otherwise `default_model`. Lets
        ops flip a single call site to a cheaper tier without redeploying.

    log_call(site_id, model, response=None, duration_ms=None,
             entity_type=None, entity_id=None, tenant_id=None,
             success=True, error_type=None) -> None
        Fire-and-forget background insert into `openai_call_log`. NEVER
        raises — wrapping the OpenAI call site MUST NOT introduce new
        failure modes. Token counts are read from `response.usage` when
        available; cost is estimated from the central PRICING dict.

PRICING is in USD per 1M tokens. Update entries as OpenAI's published
pricing changes. Models not in the table fall back to a conservative
default so we never report $0 for a real call.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

logger = logging.getLogger(__name__)


# USD per 1,000,000 tokens. (input, cached_input, output)
PRICING: dict[str, tuple[float, float, float]] = {
    'gpt-5':            (1.25, 0.125, 10.00),
    'gpt-5.4':          (1.25, 0.125, 10.00),
    'gpt-5-mini':       (0.25, 0.025,  2.00),
    'gpt-4.1':          (2.00, 0.50,   8.00),
    'gpt-4.1-mini':     (0.40, 0.10,   1.60),
    'gpt-4.1-nano':     (0.10, 0.025,  0.40),
    'gpt-4o':           (2.50, 1.25,  10.00),
    'gpt-4o-mini':      (0.15, 0.075,  0.60),
    'text-embedding-3-large': (0.13, 0.0, 0.0),
    'text-embedding-3-small': (0.02, 0.0, 0.0),
}
_PRICING_DEFAULT = (1.25, 0.125, 10.00)


def resolve_model(site_id: str, default_model: str) -> str:
    """Apply per-site env override, else return default."""
    if not site_id:
        return default_model
    key = 'MODEL_TIER_OVERRIDE_' + site_id.upper().replace('.', '_').replace('-', '_')
    override = os.environ.get(key)
    return override.strip() if override and override.strip() else default_model


def estimate_cost(model: str, input_tokens: int, cached_input_tokens: int,
                  output_tokens: int) -> Decimal:
    """Estimate USD cost for a single call."""
    rate_in, rate_cached, rate_out = PRICING.get(model, _PRICING_DEFAULT)
    billable_input = max(0, input_tokens - cached_input_tokens)
    cost = (
        (billable_input    * rate_in     / 1_000_000.0) +
        (cached_input_tokens * rate_cached / 1_000_000.0) +
        (output_tokens     * rate_out    / 1_000_000.0)
    )
    return Decimal(f'{cost:.6f}')


def _extract_usage(response: Any) -> tuple[int, int, int]:
    """Pull (input, cached_input, output) tokens from an OpenAI response.

    Tolerates Responses API + Chat Completions API + dict shapes. Returns
    zeros when usage is absent.
    """
    if response is None:
        return (0, 0, 0)
    usage = getattr(response, 'usage', None)
    if usage is None and isinstance(response, dict):
        usage = response.get('usage')
    if usage is None:
        return (0, 0, 0)

    def _g(name: str, *aliases: str) -> int:
        for n in (name, *aliases):
            if hasattr(usage, n):
                v = getattr(usage, n)
                if v is not None:
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        continue
            if isinstance(usage, dict) and usage.get(n) is not None:
                try:
                    return int(usage[n])
                except (TypeError, ValueError):
                    continue
        return 0

    input_tokens = _g('prompt_tokens', 'input_tokens')
    output_tokens = _g('completion_tokens', 'output_tokens')

    cached = 0
    details = (
        getattr(usage, 'prompt_tokens_details', None)
        or getattr(usage, 'input_tokens_details', None)
    )
    if details is None and isinstance(usage, dict):
        details = usage.get('prompt_tokens_details') or usage.get('input_tokens_details')
    if details is not None:
        if hasattr(details, 'cached_tokens'):
            try:
                cached = int(details.cached_tokens or 0)
            except (TypeError, ValueError):
                cached = 0
        elif isinstance(details, dict):
            try:
                cached = int(details.get('cached_tokens') or 0)
            except (TypeError, ValueError):
                cached = 0

    return (input_tokens, cached, output_tokens)


def _persist(payload: dict) -> None:
    """Insert one row in a short-lived app context. Swallows all errors."""
    try:
        from extensions import db
        from flask import current_app
        try:
            app = current_app._get_current_object()  # type: ignore[attr-defined]
        except RuntimeError:
            from app import app as flask_app  # late import to avoid cycle
            app = flask_app
        with app.app_context():
            from models.openai_telemetry import OpenAICallLog
            row = OpenAICallLog(**payload)
            db.session.add(row)
            db.session.commit()
    except Exception as e:
        try:
            from extensions import db as _db
            _db.session.rollback()
        except Exception:
            pass
        logger.debug(f"openai_helper.log_call persist failed: {e}")


def log_call(
    site_id: str,
    model: str,
    response: Any = None,
    duration_ms: Optional[int] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[Any] = None,
    tenant_id: Optional[str] = None,
    success: bool = True,
    error_type: Optional[str] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    cached_input_tokens: Optional[int] = None,
) -> None:
    """Fire-and-forget telemetry insert. Never raises."""
    try:
        if input_tokens is None and output_tokens is None and cached_input_tokens is None:
            in_tok, cached_tok, out_tok = _extract_usage(response)
        else:
            in_tok = int(input_tokens or 0)
            out_tok = int(output_tokens or 0)
            cached_tok = int(cached_input_tokens or 0)

        cost = estimate_cost(model, in_tok, cached_tok, out_tok)

        payload = {
            'created_at': datetime.utcnow(),
            'call_site_id': (site_id or 'unknown')[:80],
            'model': (model or 'unknown')[:80],
            'input_tokens': in_tok,
            'output_tokens': out_tok,
            'cached_input_tokens': cached_tok,
            'estimated_cost_usd': cost,
            'duration_ms': int(duration_ms) if duration_ms is not None else None,
            'tenant_id': (tenant_id[:80] if tenant_id else None),
            'entity_type': (entity_type[:40] if entity_type else None),
            'entity_id': (str(entity_id)[:80] if entity_id is not None else None),
            'success': bool(success),
            'error_type': (error_type[:120] if error_type else None),
        }

        t = threading.Thread(target=_persist, args=(payload,), daemon=True)
        t.start()
    except Exception as e:
        logger.debug(f"openai_helper.log_call dispatch failed: {e}")
