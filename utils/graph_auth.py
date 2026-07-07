"""Microsoft Graph access-token providers for Scout Genius.

Supports two auth backends:
  * ``entra``  — client-credentials flow (Railway / standalone hosting)
  * ``replit`` — Replit Outlook connector proxy (legacy Replit deployment)

Mode resolution (``GRAPH_AUTH_MODE``):
  * ``entra`` / ``replit`` — force a backend
  * ``auto`` (default) — Replit env present → replit, else Entra creds → entra
"""

from __future__ import annotations

import logging
import os
import time
from typing import Literal, Optional, Tuple
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

GraphAuthMode = Literal["entra", "replit"]

_TOKEN_CACHE: dict[str, Tuple[str, float]] = {}


def resolve_graph_auth_mode() -> GraphAuthMode:
    """Return the active Graph auth backend."""
    explicit = (os.environ.get("GRAPH_AUTH_MODE") or "auto").strip().lower()
    if explicit in ("entra", "replit"):
        return explicit  # type: ignore[return-value]

    if explicit != "auto":
        logger.warning("Unknown GRAPH_AUTH_MODE=%r — falling back to auto", explicit)

    if _replit_connector_available():
        return "replit"
    if _entra_credentials_available():
        return "entra"

    raise ConnectionError(
        "Graph auth not configured: set GRAPH_AUTH_MODE=entra with "
        "MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET, MICROSOFT_TENANT_ID "
        "(and GRAPH_MAILBOX_UPN), or run on Replit with the Outlook connector."
    )


def get_graph_mailbox_upn() -> str:
    """Mailbox UPN/SMTP for application-permission Graph reads."""
    upn = (os.environ.get("GRAPH_MAILBOX_UPN") or "apply@myticas.com").strip()
    if not upn:
        raise ConnectionError("GRAPH_MAILBOX_UPN is required for entra Graph mail access")
    return upn


def graph_user_base_path(mode: Optional[GraphAuthMode] = None) -> str:
    """Graph API user segment: ``/me`` (delegated) or ``/users/{upn}`` (app-only)."""
    auth_mode = mode or resolve_graph_auth_mode()
    if auth_mode == "replit":
        return "/me"
    return f"/users/{quote(get_graph_mailbox_upn(), safe='@.')}"


def get_graph_access_token(*, force_refresh: bool = False) -> str:
    """Return a cached Microsoft Graph bearer token."""
    mode = resolve_graph_auth_mode()
    cache_key = f"graph:{mode}"

    now = time.time()
    if not force_refresh:
        cached = _TOKEN_CACHE.get(cache_key)
        if cached and cached[1] > now + 60:
            return cached[0]

    if mode == "replit":
        token, expires_at = _fetch_replit_token()
    else:
        token, expires_at = _fetch_entra_token()

    _TOKEN_CACHE[cache_key] = (token, expires_at)
    return token


def invalidate_graph_token_cache() -> None:
    """Clear cached tokens (e.g. after a 401)."""
    _TOKEN_CACHE.clear()


def _replit_connector_available() -> bool:
    if not os.environ.get("REPLIT_CONNECTORS_HOSTNAME"):
        return False
    return bool(os.environ.get("REPL_IDENTITY") or os.environ.get("WEB_REPL_RENEWAL"))


def _entra_credentials_available() -> bool:
    return all(
        os.environ.get(key)
        for key in ("MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_TENANT_ID")
    )


def _fetch_entra_token() -> Tuple[str, float]:
    client_id = os.environ.get("MICROSOFT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("MICROSOFT_CLIENT_SECRET", "").strip()
    tenant_id = os.environ.get("MICROSOFT_TENANT_ID", "").strip()
    if not all((client_id, client_secret, tenant_id)):
        raise ConnectionError(
            "Entra Graph auth requires MICROSOFT_CLIENT_ID, "
            "MICROSOFT_CLIENT_SECRET, and MICROSOFT_TENANT_ID"
        )

    token_url = (
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    )
    resp = requests.post(
        token_url,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        logger.error(
            "Entra token request failed (%s): %s",
            resp.status_code,
            resp.text[:500],
        )
        resp.raise_for_status()

    data = resp.json()
    access_token = data.get("access_token")
    if not access_token:
        raise ConnectionError("Entra token response missing access_token")

    expires_in = data.get("expires_in", 3600)
    try:
        expires_at = time.time() + max(60, int(expires_in) - 120)
    except (TypeError, ValueError):
        expires_at = time.time() + 3500

    logger.debug("Entra Graph token acquired (expires_in=%s)", expires_in)
    return access_token, expires_at


def _fetch_replit_token() -> Tuple[str, float]:
    hostname = os.environ.get("REPLIT_CONNECTORS_HOSTNAME")
    repl_identity = os.environ.get("REPL_IDENTITY")
    web_repl_renewal = os.environ.get("WEB_REPL_RENEWAL")

    if repl_identity:
        x_replit_token = f"repl {repl_identity}"
    elif web_repl_renewal:
        x_replit_token = f"depl {web_repl_renewal}"
    else:
        raise ConnectionError("GraphMail: No Replit identity token available")

    if not hostname:
        raise ConnectionError("GraphMail: REPLIT_CONNECTORS_HOSTNAME not set")

    resp = requests.get(
        f"https://{hostname}/api/v2/connection",
        params={"include_secrets": "true", "connector_names": "outlook"},
        headers={
            "Accept": "application/json",
            "X-Replit-Token": x_replit_token,
        },
        timeout=10,
    )
    resp.raise_for_status()

    data = resp.json()
    connection = data.get("items", [None])[0] if data.get("items") else None
    if not connection:
        raise ConnectionError(
            "GraphMail: No Outlook connection found. Please connect the "
            "Outlook mailbox (apply@) in Replit settings."
        )

    settings = connection.get("settings", {})
    access_token = (
        settings.get("access_token")
        or settings.get("oauth", {}).get("credentials", {}).get("access_token")
    )
    if not access_token:
        raise ConnectionError(
            "GraphMail: No access token available. Please reconnect Outlook."
        )

    expires_at_raw = settings.get("expires_at")
    if expires_at_raw:
        try:
            from datetime import datetime

            expires_at = datetime.fromisoformat(
                expires_at_raw.replace("Z", "+00:00")
            ).timestamp()
        except (ValueError, TypeError):
            expires_at = time.time() + 3500
    else:
        expires_at = time.time() + 3500

    return access_token, expires_at
