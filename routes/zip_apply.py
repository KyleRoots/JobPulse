"""ZipRecruiter Apply webhook.

Public POST. On Qualified (SCOUT_TENANT=qualified_staffing) a delivery is
adapted into the same Bullhorn path as mail to apply@q-staffing.com.
Other tenants only store a receipt. Resume bytes are not stored.
A 200 means the delivery landed or was already recorded. A 503 means
Bullhorn processing failed and Zip should retry with the same body.
"""
import logging

from flask import Blueprint, jsonify, request

from extensions import csrf, db, limiter
from models.zip_apply_webhook import ZipApplyDelivery
from zip_apply.digest import (
    MAX_BODY_BYTES,
    body_sha256,
    parse_json_body,
    signature_header_present,
    summarize_payload,
)
from zip_apply.ingest import ingest_zip_apply, webhook_creates_candidates

logger = logging.getLogger(__name__)

zip_apply_bp = Blueprint("zip_apply", __name__)

_LANDED = ("processed", "unmatched_job", "duplicate")


@zip_apply_bp.route("/api/ziprecruiter/apply", methods=["GET", "POST"])
@csrf.exempt
@limiter.limit("60 per minute", methods=["POST"])
def zip_apply_webhook():
    if request.method == "GET":
        return jsonify({
            "status": "ok",
            "endpoint": "zip_apply",
            "accepts": "POST application/json",
        }), 200

    raw = request.get_data(cache=False, as_text=False) or b""
    if len(raw) > MAX_BODY_BYTES:
        return jsonify({"status": "rejected", "error": "body too large"}), 413

    content_type = (request.content_type or "").lower()
    if "json" not in content_type and raw[:1] not in (b"{", b"["):
        return jsonify({"status": "rejected", "error": "Content-Type must be application/json"}), 415

    payload, error = parse_json_body(raw)
    if error:
        code = 413 if error == "body too large" else 400
        return jsonify({"status": "rejected", "error": error}), code

    summary = summarize_payload(payload)
    digest = body_sha256(raw)
    creates = webhook_creates_candidates()
    existing = _prior_delivery(summary["response_id"], creates)

    ingest_status = "received"
    candidate_id = None
    submission_id = None
    ingest_error = None
    http_status = 200

    if creates and existing and existing.ingest_status in _LANDED:
        ingest_status = "duplicate"
    elif creates:
        try:
            outcome = ingest_zip_apply(payload)
        except Exception:
            logger.exception("zip_apply ingest failed job_id=%s", summary["job_id"])
            outcome = {"success": False, "message": "ingest failed"}
        if outcome.get("success"):
            candidate_id = outcome.get("candidate_id")
            submission_id = outcome.get("submission_id")
            ingest_status = "unmatched_job" if outcome.get("unmatched_job") else "processed"
        else:
            ingest_status = "failed"
            ingest_error = (outcome.get("message") or "ingest failed")[:500]
            http_status = 503

    row = ZipApplyDelivery(
        job_id=summary["job_id"],
        response_id=summary["response_id"],
        body_bytes=len(raw),
        payload_sha256=digest,
        has_name=summary["has_name"],
        has_email=summary["has_email"],
        has_phone=summary["has_phone"],
        has_resume=summary["has_resume"],
        has_profile=summary["has_profile"],
        signature_header_present=signature_header_present(request.headers),
        duplicate_of_id=existing.id if existing else None,
        field_names=summary["field_names"],
        ingest_status=ingest_status,
        bullhorn_candidate_id=candidate_id,
        bullhorn_submission_id=submission_id,
        ingest_error=ingest_error,
    )
    db.session.add(row)
    db.session.commit()

    if creates:
        try:
            from zip_apply.volume_alert import maybe_alert_zip_volume
            maybe_alert_zip_volume()
        except Exception:
            logger.exception("zip_apply volume alert failed")

    logger.info(
        "zip_apply id=%s job_id=%s bytes=%s duplicate=%s ingest=%s candidate=%s",
        row.id,
        row.job_id,
        row.body_bytes,
        bool(existing),
        ingest_status,
        candidate_id,
    )
    if http_status != 200:
        return jsonify({"status": "error", "id": row.id, "error": "processing failed"}), 503
    return jsonify({
        "status": "accepted",
        "id": row.id,
        "duplicate": bool(existing) and (not creates or ingest_status == "duplicate"),
    }), 200


def _prior_delivery(response_id, creates):
    if not response_id:
        return None
    query = ZipApplyDelivery.query.filter_by(response_id=response_id)
    if creates:
        query = query.filter(ZipApplyDelivery.ingest_status.in_(_LANDED))
    return query.order_by(ZipApplyDelivery.id.asc()).first()
