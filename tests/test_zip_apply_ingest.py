"""Zip Apply JSON is adapted onto the mailbox Bullhorn path on Qualified."""
import base64
import json

from models.scheduling import JobReferenceNumber
from models.zip_apply_webhook import ZipApplyDelivery
from zip_apply.ingest import build_mailbox_payload, resolve_bullhorn_job_id

ENDPOINT = "/api/ziprecruiter/apply"


def _pdf_payload(**extra):
    pdf = b"%PDF-1.4\n" + (b"a" * 64)
    body = {
        "job_id": "IR2WJJTS89",
        "response_id": "resp-live-1",
        "first_name": "Jamie",
        "last_name": "Applicant",
        "name": "Jamie Applicant",
        "email": "jamie@example.com",
        "phone": "5551112222",
        "resume": base64.b64encode(pdf).decode("ascii"),
        "profile": {"text_resume": "Warehouse associate, 4 years."},
    }
    body.update(extra)
    return body


def test_mailbox_payload_carries_job_resume_and_zip_source():
    built = build_mailbox_payload(_pdf_payload(), "35135")
    assert built["to"] == "apply@q-staffing.com"
    assert "noreply@ziprecruiter.com" in built["from"]
    assert "ZipRecruiter Apply (35135)" in built["subject"]
    assert "Email: jamie@example.com" in built["text"]
    assert "Phone: 5551112222" in built["text"]
    assert "Warehouse associate" in built["text"]
    assert "Message-ID: <zip-apply-resp-live-1@ziprecruiter.com>" in built["headers"]
    attachment = json.loads(built["attachments"])[0]
    assert attachment["filename"].endswith("_resume.pdf")
    assert base64.b64decode(attachment["content"]).startswith(b"%PDF")


def test_reference_number_resolves_to_bullhorn_job(app):
    with app.app_context():
        from extensions import db

        db.session.add(JobReferenceNumber(
            bullhorn_job_id="35135",
            reference_number="IR2WJJTS89",
            job_title="Picker",
        ))
        db.session.commit()
        assert resolve_bullhorn_job_id("IR2WJJTS89") == "35135"
        assert resolve_bullhorn_job_id("missing") is None


def test_qualified_webhook_records_bullhorn_ids(client, app, monkeypatch):
    monkeypatch.setenv("SCOUT_TENANT", "qualified_staffing")
    seen = {}

    def _fake(payload):
        seen["email"] = payload.get("email")
        return {
            "success": True,
            "candidate_id": 88001,
            "submission_id": 99001,
            "unmatched_job": False,
            "bullhorn_job_id": "35135",
        }

    monkeypatch.setattr("routes.zip_apply.ingest_zip_apply", _fake)
    response = client.post(
        ENDPOINT,
        data=json.dumps(_pdf_payload()),
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["duplicate"] is False
    assert seen["email"] == "jamie@example.com"
    with app.app_context():
        row = ZipApplyDelivery.query.get(body["id"])
        assert row.ingest_status == "processed"
        assert row.bullhorn_candidate_id == 88001
        assert row.bullhorn_submission_id == 99001
        assert "jamie@example.com" not in (row.field_names or "")
        assert "JVBERi0" not in (row.field_names or "")


def test_qualified_retry_after_success_does_not_ingest_again(client, monkeypatch):
    monkeypatch.setenv("SCOUT_TENANT", "qualified_staffing")
    calls = {"n": 0}

    def _fake(_payload):
        calls["n"] += 1
        return {"success": True, "candidate_id": 1, "submission_id": 2, "unmatched_job": False}

    monkeypatch.setattr("routes.zip_apply.ingest_zip_apply", _fake)
    payload = _pdf_payload(response_id="resp-once")
    first = client.post(ENDPOINT, data=json.dumps(payload), content_type="application/json")
    second = client.post(ENDPOINT, data=json.dumps(payload), content_type="application/json")
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json()["duplicate"] is True
    assert calls["n"] == 1


def test_qualified_processing_failure_asks_for_retry(client, app, monkeypatch):
    monkeypatch.setenv("SCOUT_TENANT", "qualified_staffing")
    monkeypatch.setattr(
        "routes.zip_apply.ingest_zip_apply",
        lambda _payload: {"success": False, "message": "bullhorn unavailable"},
    )
    response = client.post(
        ENDPOINT,
        data=json.dumps(_pdf_payload(response_id="resp-fail")),
        content_type="application/json",
    )
    assert response.status_code == 503
    with app.app_context():
        row = ZipApplyDelivery.query.filter_by(response_id="resp-fail").one()
        assert row.ingest_status == "failed"
        assert row.bullhorn_candidate_id is None


def test_unmatched_reference_still_accepts(client, app, monkeypatch):
    monkeypatch.setenv("SCOUT_TENANT", "qualified_staffing")
    monkeypatch.setattr(
        "routes.zip_apply.ingest_zip_apply",
        lambda _payload: {
            "success": True,
            "candidate_id": 77,
            "submission_id": None,
            "unmatched_job": True,
        },
    )
    response = client.post(
        ENDPOINT,
        data=json.dumps(_pdf_payload(response_id="resp-unmatched")),
        content_type="application/json",
    )
    assert response.status_code == 200
    with app.app_context():
        row = ZipApplyDelivery.query.filter_by(response_id="resp-unmatched").one()
        assert row.ingest_status == "unmatched_job"
        assert row.bullhorn_candidate_id == 77
