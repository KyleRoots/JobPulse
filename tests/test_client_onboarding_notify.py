"""Unit tests for Myticas client onboarding notify (eligibility + routing)."""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("SESSION_SECRET", "test-secret")

from client_onboarding_notify.eligibility import (  # noqa: E402
    is_company_eligible,
    is_interview_appointment,
    resolve_sales_rep,
    sales_rep_picker_user_id,
)
from client_onboarding_notify.email import build_recipients, render_email_html  # noqa: E402
from client_onboarding_notify.worker import (  # noqa: E402
    company_id_from_appointment,
    company_id_from_sendout,
)


def test_positive_status_blank_type_ok():
    ok, reason = is_company_eligible({"status": "Qualified", "customText1": ""})
    assert ok is True
    assert reason == ""


def test_active_account_client_type_ok():
    ok, _ = is_company_eligible({"status": "Active Account", "customText1": "Client"})
    assert ok is True


def test_unqualified_skipped():
    ok, reason = is_company_eligible({"status": "Unqualified", "customText1": "Client"})
    assert ok is False
    assert "status_not_positive" in reason


def test_vendor_type_skipped_even_if_qualified():
    ok, reason = is_company_eligible({"status": "Qualified", "customText1": "Vendor"})
    assert ok is False
    assert "type_excluded" in reason


def test_msp_and_former_client_skipped():
    assert is_company_eligible({"status": "Proposal", "customText1": "MSP"})[0] is False
    assert is_company_eligible({"status": "Negotiation", "customText1": "Former Client"})[0] is False


def test_picker_id_from_custom_text3():
    assert sales_rep_picker_user_id({"customText3": "20"}) == 20


def test_sales_rep_prefers_picker_email_over_display_name():
    company = {
        "customText3": "20",
        "customText6": ["Leslie Kennedy"],
    }

    def _fail_search(*_):
        raise AssertionError("must not fall back to name search")

    sales = resolve_sales_rep(
        company,
        fetch_user_by_id=lambda uid: {
            "id": uid,
            "email": "jbocek@stsigroup.com",
            "firstName": "Josh",
            "lastName": "Bocek",
        },
        search_users_by_name=_fail_search,
    )
    assert sales["email"] == "jbocek@stsigroup.com"
    assert sales["source"] == "customText3"


def test_sales_rep_falls_back_to_display_name_match():
    company = {"customText3": "", "customText6": "Jasmine Harvey"}
    sales = resolve_sales_rep(
        company,
        fetch_user_by_id=lambda *_: None,
        search_users_by_name=lambda first, last: [
            {
                "id": 99,
                "firstName": first,
                "lastName": last,
                "email": "jharvey@myticas.com",
                "enabled": True,
            }
        ],
    )
    assert sales["email"] == "jharvey@myticas.com"
    assert sales["source"] == "customText6"


def test_sales_rep_missing_still_allows_accounting():
    sales = resolve_sales_rep(
        {"customText3": "", "customText6": ""},
        fetch_user_by_id=lambda *_: None,
        search_users_by_name=lambda *_: [],
    )
    assert sales["email"] is None
    assert "not set" in (sales["note"] or "")


def test_interview_type_filter():
    assert is_interview_appointment({"type": "Interview"}) is True
    assert is_interview_appointment({"type": "Candidate Interview"}) is True
    assert is_interview_appointment({"type": "Meeting"}) is False
    assert is_interview_appointment({"type": ""}) is False


def test_company_from_sendout_and_appointment():
    sendout = {"clientCorporation": {"id": 53914}}
    assert company_id_from_sendout(sendout) == 53914
    appt = {"jobOrder": {"id": 1, "clientCorporation": {"id": 53912}}}
    assert company_id_from_appointment(appt) == 53912
    appt_contact = {
        "jobOrder": None,
        "clientContactReference": {"id": 9, "clientCorporation": {"id": 53913}},
    }
    assert company_id_from_appointment(appt_contact) == 53913


def test_test_mode_routes_only_to_kyle(monkeypatch):
    monkeypatch.setenv("CLIENT_OB_NOTIFY_LIVE", "false")
    monkeypatch.setenv("CLIENT_OB_NOTIFY_TEST_EMAIL", "kroots@myticas.com")
    monkeypatch.setenv("CLIENT_OB_NOTIFY_ACCOUNTING_EMAIL", "accounting@myticas.com")
    rec = build_recipients("jharvey@myticas.com")
    assert rec["live"] is False
    assert rec["to"] == "kroots@myticas.com"
    assert rec["cc"] == []
    assert rec["bcc"] == []
    assert rec["intended_to"] == "accounting@myticas.com"
    assert rec["intended_cc"] == "jharvey@myticas.com"


def test_live_mode_to_accounting_cc_sales(monkeypatch):
    monkeypatch.setenv("CLIENT_OB_NOTIFY_LIVE", "true")
    rec = build_recipients("jharvey@myticas.com")
    assert rec["to"] == "accounting@myticas.com"
    assert rec["cc"] == ["jharvey@myticas.com"]
    assert rec["bcc"] == ["kroots@myticas.com"]


def test_html_mentions_billing_setup_and_checklist():
    html_body = render_email_html({
        "company_name": "Kent Worldwide",
        "company_id": 53914,
        "company_status": "Active Account",
        "company_type": "Client",
        "trigger_label": "Client Submission (Sendout)",
        "job_title": "Designer",
        "candidate_name": "Jane Doe",
        "sales_rep_name": "Leslie Kennedy",
        "sales_rep_email": "jbocek@stsigroup.com",
        "intended_to": "accounting@myticas.com",
        "intended_cc": "jbocek@stsigroup.com",
        "company_url": "https://example/company/53914",
        "live": False,
    })
    assert "Billing Profiles" in html_body
    assert "Invoice Terms" in html_body
    assert "Onboarding" in html_body
    assert "TEST MODE" in html_body
    assert "Recruiting has started" in html_body
    assert "This is a reminder only" not in html_body
    assert "Scout Genius" not in html_body
    assert "\u2014" not in html_body
    assert "Leslie Kennedy (jbocek@stsigroup.com)" in html_body


def test_already_notified_short_circuits():
    from app import app, db  # noqa: E402
    from models.client_onboarding_notify import ClientOnboardingNotifyLog  # noqa: E402
    from client_onboarding_notify import worker as worker_mod  # noqa: E402

    with app.app_context():
        db.create_all()
        ClientOnboardingNotifyLog.query.delete()
        db.session.commit()
        db.session.add(ClientOnboardingNotifyLog(
            bullhorn_company_id=53914,
            trigger_type="sendout",
            email_sent=True,
        ))
        db.session.commit()
        bh = MagicMock()
        token = worker_mod.process_company(
            bh, 53914, trigger_type="interview", trigger_entity_id=1
        )
        assert token == "already_notified"
        bh.session.get.assert_not_called()
        ClientOnboardingNotifyLog.query.delete()
        db.session.commit()


def test_poll_disabled(monkeypatch):
    from client_onboarding_notify import worker as worker_mod  # noqa: E402

    monkeypatch.setenv("CLIENT_OB_NOTIFY_ENABLED", "false")
    summary = worker_mod.poll_and_process(MagicMock())
    assert summary["enabled"] is False
    assert summary["events_drained"] == 0
