"""Send the Finance/Sales client onboarding reminder (SendGrid)."""
from __future__ import annotations

import html
import logging
from typing import Any, Dict, Optional

from client_onboarding_notify.config import (
    accounting_email,
    bcc_email,
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
    intended_bcc = (bcc_email() or "").strip().lower() or None
    live = is_live()
    if live:
        cc = []
        if intended_cc and intended_cc != intended_to:
            cc.append(intended_cc)
        bcc = []
        if intended_bcc and intended_bcc not in {intended_to, intended_cc}:
            bcc.append(intended_bcc)
        return {
            "live": True,
            "to": intended_to,
            "cc": cc,
            "bcc": bcc,
            "intended_to": intended_to,
            "intended_cc": intended_cc,
            "intended_bcc": intended_bcc,
        }
    return {
        "live": False,
        "to": test_email(),
        "cc": [],
        "bcc": [],
        "intended_to": intended_to,
        "intended_cc": intended_cc,
        "intended_bcc": intended_bcc,
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


def _blank(value: Any, fallback: str = "Not listed") -> str:
    text = str(value or "").strip()
    return html.escape(text) if text else fallback


def _sales_rep_line(name: Any, email: Any) -> str:
    name_s = str(name or "").strip()
    email_s = str(email or "").strip()
    if name_s and email_s:
        return f"{html.escape(name_s)} ({html.escape(email_s)})"
    if email_s:
        return html.escape(email_s)
    if name_s:
        return html.escape(name_s)
    return "Not listed"


def render_email_html(context: Dict[str, Any]) -> str:
    company = _blank(context.get("company_name"), "Unknown company")
    company_id = html.escape(str(context.get("company_id") or ""))
    status = _blank(context.get("company_status"))
    ctype = _blank(context.get("company_type"), "Blank")
    trigger = _blank(
        context.get("trigger_label") or context.get("trigger_type")
    )
    job_title = _blank(context.get("job_title"))
    candidate = _blank(context.get("candidate_name"))
    sales_line = _sales_rep_line(
        context.get("sales_rep_name"), context.get("sales_rep_email")
    )
    intended_to = html.escape(context.get("intended_to") or "")
    intended_cc = html.escape(context.get("intended_cc") or "none")
    intended_bcc = html.escape(context.get("intended_bcc") or "none")
    note = html.escape(context.get("sales_rep_note") or "")
    bh_url = html.escape(context.get("company_url") or "")
    live = bool(context.get("live"))

    test_banner = ""
    if not live:
        test_banner = (
            "<p style='background:#fff3cd;padding:12px;border:1px solid #ffc107'>"
            "<strong>TEST MODE.</strong> This copy was sent only to Kyle. "
            f"When live, it will go to {intended_to}"
            f"{f', copy {intended_cc}' if intended_cc and intended_cc != 'none' else ''}"
            f"{f', and BCC {intended_bcc}' if intended_bcc and intended_bcc != 'none' else ''}."
            "</p>"
        )

    sales_note_html = f"<p>{note}</p>" if note else ""
    if bh_url:
        link_html = (
            f"<p>Open the company in Bullhorn: "
            f"<a href=\"{bh_url}\">{company} (#{company_id})</a></p>"
        )
    else:
        link_html = f"<p>Company: {company} (#{company_id})</p>"

    return f"""
<html><body style="font-family:Calibri,Arial,sans-serif;font-size:14px;line-height:1.45;color:#222">
{test_banner}
<p>Hello Accounting,</p>
<p>
Recruiting has started with this Myticas client (a Client Submission or an
Interview). Please create the <strong>Location</strong>,
<strong>Billing Profiles</strong>, and <strong>Invoice Terms</strong> in
Bullhorn before a Placement is opened and before any hours are entered.
</p>
<p>
Sales, please complete the attached <strong>Myticas New Client Onboarding
Checklist</strong> (Ottawa) if you have not already.
</p>
{link_html}
<ul>
  <li>Status: {status}</li>
  <li>Type: {ctype}</li>
  <li>What triggered this email: {trigger}</li>
  <li>Job: {job_title}</li>
  <li>Candidate: {candidate}</li>
  <li>Sales representative: {sales_line}</li>
</ul>
{sales_note_html}
</body></html>
""".strip()


def send_notify_email(context: Dict[str, Any]) -> bool:
    from email_service import EmailService

    recipients = build_recipients(context.get("sales_rep_email"))
    context = {**context, **recipients}
    html_body = render_email_html(context)
    company = context.get("company_name") or f"Company #{context.get('company_id')}"
    subject = f"New client onboarding: {company}"
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
        bcc_emails=recipients.get("bcc") or None,
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
