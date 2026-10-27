"""Unknown Zip references, the Scout review list, and the hourly alert."""
import json
from datetime import datetime, timedelta

from email_inbound_service.extraction_mixin import (
    parenthetical_job_id,
    resolved_inbound_job_id,
    webhook_bullhorn_job_id,
)
from models.ats import ParsedEmail
from models.user import User
from models.zip_apply_webhook import ZipApplyDelivery
from zip_apply.ingest import ingest_zip_apply
from zip_apply.volume_alert import maybe_alert_zip_volume


def _payload(**extra):
    body = {
        "job_id": "HYQC33CAXL",
        "response_id": "resp-short",
        "name": "Jamie Applicant",
        "email": "jamie@example.com",
    }
    body.update(extra)
    return body


def test_email_subject_still_ignores_two_and_three_digit_ids():
    assert parenthetical_job_id("ZipRecruiter Apply (42) - Pat Example") is None
    assert parenthetical_job_id("ZipRecruiter Apply (547) - Pat Example") is None
    assert parenthetical_job_id("ZipRecruiter Apply (35135) - Pat Example") == 35135
    assert webhook_bullhorn_job_id({}) is None
    assert webhook_bullhorn_job_id({"subject": "ZipRecruiter Apply (42) - Pat"}) is None


def test_webhook_hands_two_and_three_digit_ids_straight_through():
    def _extractor(subject, body):
        return parenthetical_job_id(subject)

    assert resolved_inbound_job_id(
        {"_scout_bullhorn_job_id": "42"},
        "ZipRecruiter Apply (42) - Pat Example",
        "",
        _extractor,
    ) == 42
    assert resolved_inbound_job_id(
        {"_scout_bullhorn_job_id": "547"},
        "ZipRecruiter Apply (547) - Pat Example",
        "",
        _extractor,
    ) == 547
    assert resolved_inbound_job_id(
        {},
        "ZipRecruiter Apply (42) - Pat Example",
        "",
        _extractor,
    ) is None
    assert resolved_inbound_job_id(
        {},
        "Role (35135) - Pat Example",
        "",
        _extractor,
    ) == 35135


def test_ingest_puts_the_resolved_id_on_the_mailbox(monkeypatch):
    seen = {}

    class _Service:
        def process_email(self, mailbox):
            seen["mailbox"] = mailbox
            return {"success": True, "candidate_id": 9, "submission_id": 8}

    monkeypatch.setattr(
        "zip_apply.ingest.resolve_bullhorn_job_id",
        lambda reference: "42",
    )
    monkeypatch.setattr("email_inbound_service.EmailInboundService", _Service)
    result = ingest_zip_apply(_payload())
    assert seen["mailbox"]["_scout_bullhorn_job_id"] == "42"
    assert "(42)" in seen["mailbox"]["subject"]
    assert result["unmatched_job"] is False
    assert result["bullhorn_job_id"] == "42"


def _delivery(job_id, response_id, status, candidate_id, when):
    return ZipApplyDelivery(
        job_id=job_id,
        response_id=response_id,
        body_bytes=20,
        payload_sha256="b" * 64,
        ingest_status=status,
        bullhorn_candidate_id=candidate_id,
        created_at=when,
    )


def test_review_list_shows_unmatched_applicants_to_admins(authenticated_client, app):
    when = datetime(2026, 10, 26, 17, 5)
    with app.app_context():
        from extensions import db

        db.session.add(_delivery("G39HREJ2WE", "resp-review", "unmatched_job", 77, when))
        db.session.add(_delivery("MAPPEDREF1", "resp-ok", "processed", 78, when))
        db.session.add(ParsedEmail(
            sender_email="noreply@ziprecruiter.com",
            recipient_email="apply@q-staffing.com",
            subject="ZipRecruiter Apply - Pat Example",
            candidate_name="Pat Example",
            bullhorn_candidate_id=77,
            status="completed",
        ))
        db.session.commit()
    response = authenticated_client.get("/scout-inbound/zip-unmatched")
    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "G39HREJ2WE" in page
    assert "Pat Example" in page
    assert "MAPPEDREF1" not in page
    assert "entity=Candidate&amp;id=77" in page


def test_non_admin_cannot_open_the_unmatched_list(client, app):
    with app.app_context():
        from extensions import db

        from werkzeug.security import generate_password_hash

        user = User(
            username="recruiterzip",
            email="recruiterzip@test.com",
            is_admin=False,
        )
        user.password_hash = generate_password_hash("testpassword123", method="pbkdf2:sha256")
        user.set_modules(["scout_inbound"])
        db.session.add(user)
        db.session.add(_delivery(
            "SECRETREF1",
            "resp-hidden",
            "unmatched_job",
            79,
            datetime(2026, 10, 26, 17, 6),
        ))
        db.session.commit()
    login = client.post("/login", data={
        "username": "recruiterzip",
        "password": "testpassword123",
    }, follow_redirects=False)
    assert login.status_code in (200, 302)
    response = client.get("/scout-inbound/zip-unmatched")
    assert response.status_code == 403
    assert "SECRETREF1" not in response.get_data(as_text=True)


def _clear_zip_rows():
    from extensions import db
    from models.scheduling import GlobalSettings

    ZipApplyDelivery.query.delete()
    GlobalSettings.query.filter_by(setting_key="zip_apply_volume_alert_at").delete()
    db.session.commit()


def test_hourly_alert_fires_above_the_limits_and_cools_down(app, monkeypatch):
    monkeypatch.setenv("SCOUT_TENANT", "qualified_staffing")
    sent = []
    monkeypatch.setattr(
        "zip_apply.volume_alert.deliver_zip_volume_alert",
        lambda to_email, subject, html: sent.append((to_email, subject, html)),
    )
    moment = datetime(2026, 10, 26, 17, 30)
    with app.app_context():
        from extensions import db

        _clear_zip_rows()
        for index in range(10):
            db.session.add(_delivery(
                f"REF{index}",
                f"resp-{index}",
                "unmatched_job",
                1000 + index,
                moment - timedelta(minutes=5),
            ))
        db.session.commit()
        assert maybe_alert_zip_volume(moment) == "ok"
        db.session.add(_delivery(
            "REF10",
            "resp-10",
            "unmatched_job",
            1010,
            moment - timedelta(minutes=1),
        ))
        db.session.commit()
        assert maybe_alert_zip_volume(moment) == "sent"
        assert sent and "11 unmatched" in sent[0][2]
        assert maybe_alert_zip_volume(moment + timedelta(minutes=10)) == "cooled"
        assert len(sent) == 1


def test_hourly_alert_fires_on_total_volume_and_skips_other_tenants(app, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "zip_apply.volume_alert.deliver_zip_volume_alert",
        lambda to_email, subject, html: sent.append(subject),
    )
    moment = datetime(2026, 10, 26, 18, 0)
    with app.app_context():
        from extensions import db

        _clear_zip_rows()
        for index in range(101):
            db.session.add(_delivery(
                "SAMEJOB",
                f"burst-{index}",
                "processed",
                2000 + index,
                moment - timedelta(minutes=2),
            ))
        db.session.commit()
        monkeypatch.delenv("SCOUT_TENANT", raising=False)
        assert maybe_alert_zip_volume(moment) == "skipped"
        monkeypatch.setenv("SCOUT_TENANT", "qualified_staffing")
        assert maybe_alert_zip_volume(moment) == "sent"
        assert sent == ["ZipRecruiter apply spike on Qualified"]


def test_webhook_counts_the_new_row_toward_the_alert(client, app, monkeypatch):
    monkeypatch.setenv("SCOUT_TENANT", "qualified_staffing")
    calls = []
    monkeypatch.setattr(
        "zip_apply.volume_alert.maybe_alert_zip_volume",
        lambda: calls.append("checked") or "ok",
    )
    monkeypatch.setattr(
        "routes.zip_apply.ingest_zip_apply",
        lambda _payload: {
            "success": True,
            "candidate_id": 5,
            "submission_id": None,
            "unmatched_job": True,
        },
    )
    response = client.post(
        "/api/ziprecruiter/apply",
        data=json.dumps({"job_id": "X", "response_id": "alert-row", "name": "A"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert calls == ["checked"]
