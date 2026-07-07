"""
Graph Mail Service — Pull-based inbound applicant ingestion via Microsoft Graph.

EMERGENCY CONTINGENCY: Replit's GCE load balancer truncates large inbound POST
bodies before they reach gunicorn, so SendGrid Inbound Parse cannot reliably
deliver applicant emails (résumé attachments) to the webhook. The SAME emails
arrive 100% intact in the apply@myticas.com Office 365 mailbox, so we PULL them
from there via Microsoft Graph and feed them into the EXISTING inbound pipeline
(EmailInboundService.process_email) unchanged.

Auth backends (see ``utils.graph_auth``):
  * **replit** — Replit Outlook connector (delegated OAuth, ``/me`` endpoints)
  * **entra**  — Entra app registration (client credentials, ``/users/{mailbox}``)

This module ONLY fetches messages and adapts them into the SendGrid-shaped
payload dict the existing pipeline already consumes. No résumé parsing, source
detection, or Bullhorn logic is re-implemented here.
"""

import base64
import logging
import os
import time
from typing import Dict, List, Optional

import requests

from utils.graph_auth import (
    get_graph_access_token,
    graph_user_base_path,
    invalidate_graph_token_cache,
    resolve_graph_auth_mode,
)

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

# Fields we need to reconstruct the webhook payload. internetMessageId becomes
# the Message-ID used for dedupe; body carries text/html; from/to/subject feed
# source detection and candidate extraction exactly as the webhook did.
_MESSAGE_SELECT = (
    "id,internetMessageId,receivedDateTime,subject,from,toRecipients,"
    "hasAttachments,body,bodyPreview"
)

_MAX_429_RETRIES = 4


class GraphMailService:
    """Read the connected applicant mailbox via Microsoft Graph and adapt each
    message into the existing inbound-email payload shape."""

    def __init__(self):
        self._auth_mode = resolve_graph_auth_mode()

    def _user_base(self) -> str:
        return graph_user_base_path(self._auth_mode)

    # ── Auth ──────────────────────────────────────────────────────────────
    def _get_access_token(self) -> str:
        return get_graph_access_token()

    # ── HTTP helpers (401 refresh + 429 backoff) ──────────────────────────
    def _request(self, method: str, url: str, *, params: Optional[Dict] = None,
                 accept_json: bool = True) -> requests.Response:
        attempt = 0
        while True:
            token = self._get_access_token()
            headers = {"Authorization": f"Bearer {token}"}
            if accept_json:
                headers["Accept"] = "application/json"
            resp = requests.request(
                method, url, headers=headers, params=params, timeout=60
            )
            if resp.status_code == 401:
                invalidate_graph_token_cache()
                token = self._get_access_token()
                headers["Authorization"] = f"Bearer {token}"
                resp = requests.request(
                    method, url, headers=headers, params=params, timeout=60
                )
            if resp.status_code == 429 and attempt < _MAX_429_RETRIES:
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else 2 ** attempt
                except (ValueError, TypeError):
                    delay = 2 ** attempt
                delay = min(delay, 30)
                logger.warning(
                    "GraphMail: 429 throttled, backing off %.1fs (attempt %d)",
                    delay, attempt + 1,
                )
                time.sleep(delay)
                attempt += 1
                continue
            resp.raise_for_status()
            return resp

    def _get_json(self, endpoint_or_url: str, params: Optional[Dict] = None) -> Dict:
        url = (endpoint_or_url if endpoint_or_url.startswith("http")
               else f"{GRAPH_BASE_URL}{endpoint_or_url}")
        return self._request("GET", url, params=params).json()

    # ── Public API ────────────────────────────────────────────────────────
    def get_connected_address(self) -> Optional[str]:
        """Return the mailbox address Graph is reading from."""
        try:
            if self._auth_mode == "entra":
                return os.environ.get("GRAPH_MAILBOX_UPN", "apply@myticas.com").strip()
            me = self._get_json("/me")
            return me.get("mail") or me.get("userPrincipalName")
        except Exception as e:  # noqa: BLE001
            logger.error(f"GraphMail: get_connected_address failed: {e}")
            return None

    def list_messages(self, since_iso: Optional[str] = None,
                      limit: int = 25) -> List[Dict]:
        """List inbox messages ordered oldest-first.

        Args:
            since_iso: ISO8601 UTC timestamp; only messages with
                receivedDateTime >= this are returned. None = no lower bound.
            limit: max messages to return (single page).

        Returns the raw Graph message dicts (metadata + body).
        """
        params = {
            "$select": _MESSAGE_SELECT,
            "$orderby": "receivedDateTime asc",
            "$top": str(max(1, min(limit, 100))),
        }
        if since_iso:
            params["$filter"] = f"receivedDateTime ge {since_iso}"
        data = self._get_json(
            f"{self._user_base()}/mailFolders/inbox/messages", params=params
        )
        return data.get("value", []) or []

    def list_messages_page(self, since_iso: Optional[str] = None,
                           page_size: int = 50,
                           next_link: Optional[str] = None) -> tuple:
        """Deterministic server-side paging for the one-time backfill.

        Follows Microsoft Graph's `@odata.nextLink` so the entire window is
        walked completely and in order, immune to receivedDateTime ties at a
        page boundary (which a timestamp-only cursor can stall on).

        Returns (messages, next_link). When `next_link` is given it is followed
        verbatim and `since_iso`/`page_size` are ignored (Graph bakes them in).
        """
        if next_link:
            data = self._get_json(next_link)
        else:
            params = {
                "$select": _MESSAGE_SELECT,
                "$orderby": "receivedDateTime asc",
                "$top": str(max(1, min(page_size, 100))),
            }
            if since_iso:
                params["$filter"] = f"receivedDateTime ge {since_iso}"
            data = self._get_json(
                f"{self._user_base()}/mailFolders/inbox/messages", params=params
            )
        return data.get("value", []) or [], data.get("@odata.nextLink")

    def get_message_by_internet_id(self, internet_message_id: str) -> Optional[Dict]:
        """Re-fetch a single message by its RFC 5322 Message-ID (Graph
        ``internetMessageId``), searching the whole mailbox.

        Used by the résumé-recovery tool to retrieve a message whose attachments
        failed to ingest the first time, independent of the live poller's
        high-water cursor (the message may already sit below it).

        Accepts either the raw ``<...@...>`` Message-ID OR the ``graph-id-<id>``
        fallback the adapter emits for messages that lack an internetMessageId;
        the latter is fetched directly by Graph item id. Returns the message dict
        (same shape as ``list_messages``) or None if not found.
        """
        if not internet_message_id:
            return None
        user_base = self._user_base()
        try:
            if internet_message_id.startswith("graph-id-"):
                graph_id = internet_message_id[len("graph-id-"):]
                if not graph_id:
                    return None
                return self._get_json(
                    f"{user_base}/messages/{graph_id}",
                    params={"$select": _MESSAGE_SELECT},
                )
            safe = internet_message_id.replace("'", "''")
            params = {
                "$select": _MESSAGE_SELECT,
                "$filter": f"internetMessageId eq '{safe}'",
                "$top": "1",
            }
            data = self._get_json(f"{user_base}/messages", params=params)
            items = data.get("value", []) or []
            return items[0] if items else None
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"GraphMail: get_message_by_internet_id failed for "
                f"{internet_message_id[:60]!r}: {e}"
            )
            return None

    def get_attachments(self, message_id: str) -> List[Dict]:
        """Fetch file attachments for a message as
        [{filename, content_b64, content_type}]. Handles large attachments by
        fetching raw bytes individually when contentBytes is absent."""
        out: List[Dict] = []
        user_base = self._user_base()
        try:
            data = self._get_json(f"{user_base}/messages/{message_id}/attachments")
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"GraphMail: failed to list attachments for {message_id[:40]}: {e}"
            )
            return out

        for att in data.get("value", []) or []:
            odata_type = att.get("@odata.type", "")
            if "fileAttachment" not in odata_type and att.get("contentBytes") is None:
                if "fileAttachment" not in odata_type:
                    continue
            filename = att.get("name") or "attachment"
            content_type = att.get("contentType") or "application/octet-stream"
            content_b64 = att.get("contentBytes")
            if not content_b64:
                try:
                    raw = self._request(
                        "GET",
                        f"{GRAPH_BASE_URL}{user_base}/messages/{message_id}/attachments/"
                        f"{att.get('id')}/$value",
                        accept_json=False,
                    ).content
                    content_b64 = base64.b64encode(raw).decode("ascii")
                except Exception as e:  # noqa: BLE001
                    logger.error(
                        f"GraphMail: failed to fetch raw attachment "
                        f"{filename!r} on {message_id[:40]}: {e}"
                    )
                    continue
            out.append({
                "filename": filename,
                "content": content_b64,
                "type": content_type,
            })
        return out

    @staticmethod
    def _format_address(addr_obj: Optional[Dict]) -> str:
        """Render a Graph emailAddress object as 'Name <email>' like the
        webhook's From header."""
        if not addr_obj:
            return ""
        ea = addr_obj.get("emailAddress", {}) or {}
        name = (ea.get("name") or "").strip()
        email = (ea.get("address") or "").strip()
        if name and email and name.lower() != email.lower():
            return f"{name} <{email}>"
        return email or name

    def to_payload(self, message: Dict, attachments: List[Dict]) -> Dict:
        """Adapt a Graph message (+ already-fetched attachments) into the
        SendGrid-shaped payload dict consumed by EmailInboundService.process_email.

        Keys produced: from, to, subject, text, html, headers (Message-ID),
        attachments (JSON list of base64). The existing _extract_attachments and
        Message-ID dedupe consume these verbatim.
        """
        import json

        from_addr = self._format_address(message.get("from"))
        to_recipients = message.get("toRecipients", []) or []
        to_addr = ", ".join(
            self._format_address(r) for r in to_recipients if r
        )
        subject = message.get("subject") or ""

        body = message.get("body", {}) or {}
        body_content = body.get("content") or ""
        body_type = (body.get("contentType") or "").lower()
        if body_type == "html":
            html = body_content
            text = message.get("bodyPreview") or ""
        else:
            text = body_content or message.get("bodyPreview") or ""
            html = ""

        dedupe_id = message.get("internetMessageId") or ""
        if not dedupe_id:
            graph_id = message.get("id") or ""
            if graph_id:
                dedupe_id = f"graph-id-{graph_id}"
        headers = f"Message-ID: {dedupe_id}" if dedupe_id else ""

        payload = {
            "from": from_addr,
            "to": to_addr,
            "subject": subject,
            "text": text,
            "html": html,
            "headers": headers,
        }
        if attachments:
            payload["attachments"] = json.dumps(attachments)
        return payload
