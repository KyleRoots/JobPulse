"""ZipRecruiter Apply webhook receiver.

Public POST. A 2xx response means the delivery was accepted and stored.
Candidate creation in Bullhorn is a later step, after a live payload is reviewed.
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

logger = logging.getLogger(__name__)

zip_apply_bp = Blueprint("zip_apply", __name__)


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
    existing = None
    if summary["response_id"]:
        existing = ZipApplyDelivery.query.filter_by(response_id=summary["response_id"]).first()

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
    )
    db.session.add(row)
    db.session.commit()

    logger.info(
        "zip_apply accepted id=%s job_id=%s bytes=%s duplicate=%s fields=%s",
        row.id,
        row.job_id,
        row.body_bytes,
        bool(existing),
        row.field_names,
    )
    return jsonify({
        "status": "accepted",
        "id": row.id,
        "duplicate": bool(existing),
    }), 200
