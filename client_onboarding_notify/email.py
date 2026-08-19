"""Send the Finance/Sales client onboarding reminder (SendGrid)."""
from __future__ import annotations

import html
import logging
from typing import Any, Dict, Optional

from client_onboarding_notify.config import (
    accounting_email,
    checklist_path,
    is_live,
    test_email,
)

logger = logging.getLogger(__name__)

CHECKLIST_MIME = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def build_recipients(sales_rep_email: Optional[str]) -> Dict[str, Any]:
    intended_to = accounting_email()
    intended_cc = (sales_rep_email or "").strip().lower() or None
    live = is_live()
    if live:
        return {
            "live": True,
            "to": intended_to,
            "cc": [intended_cc] if intended_cc else [],
            "intended_to": intended_to,
            "intended_cc": intended_cc,
        }
    return {
        "live": False,
        "to": test_email(),
        "cc": [],
        "intended_to": intended_to,
        "intended_cc": intended_cc,
    }


def _load_checklist() -> Optional[Dict[str, Any]]:
    path = checklist_path()
    if not path.is_file():
        logger.error(f"client_ob checklist missing at {path}")
        return None
    return {
        "filename": path.name,
        "content_type": CHECKLIST_MIME,
        "data": path.read_bytes(),
    }


def render_email_html(context: Dict[str, Any]) -> str:
    company = html.escape(context.get("company_name") or "(unknown company)")
    company_id = html.escape(str(context.get("company_id") or ""))
    status = html.escape(context.get("company_status") or "—")
    ctype = html.escape(context.get("company_type") or "(blank)")
    trigger = html.escape(context.get("trigger_label") or context.get("trigger_type") or "")
    job_title = html.escape(context.get("job_title") or "—")
    candidate = html.escape(context.get("candidate_name") or "—")
    sales_name = html.escape(context.get("sales_rep_name") or "—")
    sales_email = html.escape(context.get("sales_rep_email") or "not found")
    intended_to = html.escape(context.get("intended_to") or "")
    intended_cc = html.escape(context.get("intended_cc") or "(none)")
    note = html.escape(context.get("sales_rep_note") or "")
    bh_url = html.escape(context.get("company_url") or "")
    live = bool(context.get("live"))

    test_banner = ""
    if not live:
        test_banner = (
            "<p style='background:#fff3cd;padding:12px;border:1px solid #ffc107'>"
            "<strong>TEST MODE</strong> — this message was redirected to Kyle. "
            f"Intended To: {intended_to}. Intended CC: {intended_cc}."
            "</p>"
        )

    sales_note_html = f"<p><em>{note}</em></p>" if note else ""
    link_html = (
        f"<p>Company in Bullhorn: <a href=\"{bh_url}\">{company} #{company_id}</a></p>"
        if bh_url
        else f"<p>Company: {company} #{company_id}</p>"
    )

    return f"""
<html><body style="font-family:Calibri,Arial,sans-serif;font-size:14px;color:#222">
{test_banner}
<p>Hello Accounting,</p>
<p>
A Myticas client has a first <strong>Client Submission</strong> and/or
<strong>Interview</strong> — please create <strong>Location</strong>,
<strong>Billing Profiles</strong>, and <strong>Invoice Terms</strong> in
Bullhorn <em>before</em> a Placement is opened and before hours are entered.
</p>
<p>
Sales: please complete the attached <strong>Myticas New Client Onboarding
Checklist</strong> (Ottawa) if it is not already done.
</p>
{link_html}
<ul>
  <li>Status: {status}</li>
  <li>Type: {ctype}</li>
  <li>Trigger: {trigger}</li>
  <li>Job: {job_title}</li>
  <li>Candidate: {candidate}</li>
  <li>Sales Rep: {sales_name} &lt;{sales_email}&gt;</li>
</ul>
{sales_note_html}
<p>Scout Genius does not write to Bullhorn for this reminder — setup is still
a Finance/Sales action.</p>
<p>Thanks,<br>Scout Genius</p>
</body></html>
""".strip()


def send_notify_email(context: Dict[str, Any]) -> bool:
    from email_service import EmailService

    recipients = build_recipients(context.get("sales_rep_email"))
    context = {**context, **recipients}
    html_body = render_email_html(context)
    company = context.get("company_name") or f"Company #{context.get('company_id')}"
    subject = f"New client onboarding — {company}"
    if not recipients["live"]:
        subject = f"[TEST] {subject}"

    attachments = []
    checklist = _load_checklist()
    if checklist:
        attachments.append(checklist)

    svc = EmailService()
    result = svc.send_html_email(
        to_email=recipients["to"],
        subject=subject,
        html_content=html_body,
        notification_type="client_onboarding_notify",
        cc_emails=recipients["cc"] or None,
        attachments=attachments or None,
    )
    ok = bool(result and result.get("success"))
    if ok:
        logger.info(
            "client_ob email sent company=%s to=%s live=%s",
            context.get("company_id"),
            recipients["to"],
            recipients["live"],
        )
    else:
        logger.error(
            "client_ob email failed company=%s to=%s",
            context.get("company_id"),
            recipients["to"],
        )
    return ok
