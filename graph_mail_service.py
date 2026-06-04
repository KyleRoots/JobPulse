"""
Graph Mail Service — Pull-based inbound applicant ingestion via Microsoft Graph.

EMERGENCY CONTINGENCY: Replit's GCE load balancer truncates large inbound POST
bodies before they reach gunicorn, so SendGrid Inbound Parse cannot reliably
deliver applicant emails (résumé attachments) to the webhook. The SAME emails
arrive 100% intact in the apply@myticas.com Office 365 mailbox, so we PULL them
from there via Microsoft Graph and feed them into the EXISTING inbound pipeline
(EmailInboundService.process_email) unchanged.

Auth: Replit-managed Outlook connector (delegated OAuth), signed in AS the
apply@myticas.com mailbox — so the mailbox is reachable via the /me endpoints
with the plain Mail.Read / Mail.ReadWrite scopes the connector grants. Token
management mirrors onedrive_service.py.

This module ONLY fetches messages and adapts them into the SendGrid-shaped
payload dict the existing pipeline already consumes. No résumé parsing, source
detection, or Bullhorn logic is re-implemented here.
"""

import base64
import logging
import os
import time
from datetime import datetime
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
CONNECTOR_NAME = "outlook"

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
        self._cached_token = None
        self._token_expires_at = 0

    # ── Auth ──────────────────────────────────────────────────────────────
    def _get_access_token(self) -> str:
        now = time.time()
        if self._cached_token and self._token_expires_at > now + 60:
            return self._cached_token

        hostname = os.environ.get('REPLIT_CONNECTORS_HOSTNAME')
        repl_identity = os.environ.get('REPL_IDENTITY')
        web_repl_renewal = os.environ.get('WEB_REPL_RENEWAL')

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
            params={"include_secrets": "true", "connector_names": CONNECTOR_NAME},
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

        expires_at = settings.get("expires_at")
        if expires_at:
            try:
                self._token_expires_at = datetime.fromisoformat(
                    expires_at.replace("Z", "+00:00")
                ).timestamp()
            except (ValueError, TypeError):
                self._token_expires_at = now + 3500
        else:
            self._token_expires_at = now + 3500

        self._cached_token = access_token
        return access_token

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
                # Force a token refresh once, then retry.
                self._cached_token = None
                self._token_expires_at = 0
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
        """Return the userPrincipalName/mail of the connected account. Used to
        confirm the connector is signed in as the applicant mailbox."""
        try:
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
            # `ge` (not `gt`) so a message sharing the boundary second is never
            # skipped; message_id dedupe makes the re-check harmless.
            params["$filter"] = f"receivedDateTime ge {since_iso}"
        data = self._get_json("/me/mailFolders/inbox/messages", params=params)
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
            data = self._get_json("/me/mailFolders/inbox/messages", params=params)
        return data.get("value", []) or [], data.get("@odata.nextLink")

    def get_attachments(self, message_id: str) -> List[Dict]:
        """Fetch file attachments for a message as
        [{filename, content_b64, content_type}]. Handles large attachments by
        fetching raw bytes individually when contentBytes is absent."""
        out: List[Dict] = []
        try:
            data = self._get_json(
                f"/me/messages/{message_id}/attachments",
                params={"$select": "id,name,contentType,size,contentBytes"},
            )
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"GraphMail: failed to list attachments for {message_id[:40]}: {e}"
            )
            return out

        for att in data.get("value", []) or []:
            odata_type = att.get("@odata.type", "")
            # Only real file attachments; skip itemAttachment / reference.
            if "fileAttachment" not in odata_type and att.get("contentBytes") is None:
                # Could be an inline reference or item attachment; skip non-files.
                if "fileAttachment" not in odata_type:
                    continue
            filename = att.get("name") or "attachment"
            content_type = att.get("contentType") or "application/octet-stream"
            content_b64 = att.get("contentBytes")
            if not content_b64:
                # Large attachment: fetch raw bytes via $value.
                try:
                    raw = self._request(
                        "GET",
                        f"{GRAPH_BASE_URL}/me/messages/{message_id}/attachments/"
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

        # internetMessageId is the canonical <...@...> Message-ID; the pipeline
        # parses it out of the headers blob for its UNIQUE dedupe. If a message
        # ever lacks it, fall back to the Graph message `id` (always present,
        # stable per mailbox) so the pipeline ALWAYS has a dedupe key — otherwise
        # a re-pull/backfill of that message could double-create a Bullhorn
        # candidate.
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
