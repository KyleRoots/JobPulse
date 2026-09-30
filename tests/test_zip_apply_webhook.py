"""Zip Apply webhook accepts JSON and records field presence, not resume bytes."""
import json

from models.zip_apply_webhook import ZipApplyDelivery

ENDPOINT = "/api/ziprecruiter/apply"


def test_get_is_a_ready_check(client):
    response = client.get(ENDPOINT)
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["endpoint"] == "zip_apply"


def test_post_json_is_accepted_without_storing_resume(client, app):
    payload = {
        "job_id": "PQS1SM6Z5H",
        "response_id": "resp-1",
        "name": "Test Candidate",
        "email": "candidate@example.com",
        "phone": "5551112222",
        "resume": "JVBERi0xLjQ=" * 20,
        "profile": {"text_resume": "Worked nights.", "job_records": []},
    }
    response = client.post(
        ENDPOINT,
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "accepted"
    assert body["duplicate"] is False

    with app.app_context():
        row = ZipApplyDelivery.query.get(body["id"])
        assert row.job_id == "PQS1SM6Z5H"
        assert row.response_id == "resp-1"
        assert row.has_name and row.has_email and row.has_phone
        assert row.has_resume and row.has_profile
        assert "JVBERi0" not in (row.field_names or "")
        assert "candidate@example.com" not in (row.field_names or "")


def test_repeat_response_id_is_still_accepted(client):
    payload = {"job_id": "REF1", "response_id": "same-response", "name": "Again"}
    first = client.post(ENDPOINT, data=json.dumps(payload), content_type="application/json")
    second = client.post(ENDPOINT, data=json.dumps(payload), content_type="application/json")
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json()["duplicate"] is True


def test_non_json_is_rejected(client):
    response = client.post(ENDPOINT, data="not-json", content_type="text/plain")
    assert response.status_code == 415
