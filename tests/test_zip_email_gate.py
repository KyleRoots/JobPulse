"""Zip notification emails stay on until ZIP_EMAIL_INTAKE_ENABLED is turned off."""
from tests.test_inbound_noise_gate import _make_service


def test_zip_email_is_still_processed_by_default(app):
    from models import ParsedEmail

    with app.app_context():
        svc = _make_service(
            monkeypatch=None,
            attachments=[],
            resume_file=None,
            source="ZipRecruiter Job Board",
        )
        result = svc.process_email({
            "from": "ZipRecruiter <noreply@ziprecruiter.com>",
            "to": "apply@q-staffing.com",
            "subject": "Great Match: Jane Candidate for 'Material Handler (46939)'",
            "text": "",
            "html": "",
            "headers": "",
        })
        assert result["ignored"] is False
        row = ParsedEmail.query.get(result["parsed_email_id"])
        assert "ZIP_EMAIL_INTAKE_ENABLED" not in (row.processing_notes or "")


def test_zip_email_is_ignored_only_when_the_switch_is_off(app, monkeypatch):
    from models import ParsedEmail

    monkeypatch.setenv("ZIP_EMAIL_INTAKE_ENABLED", "false")
    with app.app_context():
        svc = _make_service(
            monkeypatch=None,
            attachments=[],
            resume_file=None,
            source="ZipRecruiter Job Board",
        )
        result = svc.process_email({
            "from": "ZipRecruiter <noreply@ziprecruiter.com>",
            "to": "apply@q-staffing.com",
            "subject": "Great Match: Jane Candidate for 'Material Handler (46939)'",
            "text": "",
            "html": "",
            "headers": "",
        })
        assert result["ignored"] is True
        assert result["success"] is False
        svc._notify_admin_parse_failure.assert_not_called()
        row = ParsedEmail.query.get(result["parsed_email_id"])
        assert row.status == "ignored"
        assert "webhook" in (row.processing_notes or "").lower() or "Apply webhook" in (row.processing_notes or "")


def test_webhook_payload_is_not_ignored_when_zip_email_is_off(app, monkeypatch):
    monkeypatch.setenv("ZIP_EMAIL_INTAKE_ENABLED", "false")
    with app.app_context():
        svc = _make_service(
            monkeypatch=None,
            attachments=[],
            resume_file=None,
            source="ZipRecruiter Job Board",
        )
        result = svc.process_email({
            "from": "ZipRecruiter <noreply@ziprecruiter.com>",
            "to": "apply@q-staffing.com",
            "subject": "ZipRecruiter Apply (35135) - Jamie Applicant",
            "text": "Name: Jamie Applicant\nEmail: jamie@example.com\n",
            "html": "",
            "headers": "Message-ID: <zip-apply-resp@ziprecruiter.com>\nX-Scout-Zip-Apply: webhook\n",
        })
        assert result["ignored"] is False


def test_plain_text_profile_becomes_resume_html(tmp_path):
    from resume_parser import ResumeParser

    path = tmp_path / "Applicant_resume.txt"
    path.write_text("Picker at Acme Warehouse\n\nPicked orders for four years.\n", encoding="utf-8")
    result = ResumeParser().parse_resume(str(path), quick_mode=True, skip_cache=True)
    assert result["success"] is True
    assert "Picker at Acme Warehouse" in result["raw_text"]
    assert "<p>" in result["formatted_html"]
    assert "Acme Warehouse" in result["formatted_html"]
