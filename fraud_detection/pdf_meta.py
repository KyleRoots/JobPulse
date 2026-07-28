"""PDF metadata helpers for résumé document forensics."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger("fraud_detection.pdf_meta")

_META_KEYS = ("author", "creator", "producer", "creationDate", "modDate")


def extract_pdf_metadata(file_content: Optional[bytes]) -> Dict[str, str]:
    """Return bounded PDF metadata fields via PyMuPDF (empty dict on failure)."""
    if not file_content or not file_content.startswith(b"%PDF"):
        return {}
    try:
        import fitz

        doc = fitz.open(stream=file_content, filetype="pdf")
        meta = doc.metadata or {}
        doc.close()
        out: Dict[str, str] = {}
        for key in _META_KEYS:
            val = meta.get(key) or meta.get(key.lower()) or ""
            val = re.sub(r"\s+", " ", str(val)).strip()
            if val:
                out[key.lower()] = val[:200]
        return out
    except Exception as exc:
        logger.debug("PDF metadata extract failed: %s", exc)
        return {}


def pdf_signature(meta: Optional[Dict[str, str]]) -> str:
    """Stable Author|Creator|Producer fingerprint (empty if insufficient)."""
    if not meta:
        return ""
    author = (meta.get("author") or "").strip().lower()
    creator = (meta.get("creator") or "").strip().lower()
    producer = (meta.get("producer") or "").strip().lower()
    # Skip empty / generic tool-only signatures that over-match.
    if not author and not creator:
        return ""
    generic = {
        "",
        "microsoft® word",
        "microsoft word",
        "word",
        "adobe pdf library",
    }
    if author in generic and creator in generic:
        return ""
    parts = [author or "-", creator or "-", producer or "-"]
    return "|".join(parts)[:240]


def pdf_mod_is_recent(meta: Optional[Dict[str, str]], days: int = 14) -> bool:
    """True when ModDate is within ``days`` of now (best-effort parse)."""
    if not meta:
        return False
    raw = meta.get("moddate") or meta.get("modDate") or ""
    if not raw:
        return False
    # PDF dates often look like D:20260728120000-04'00'
    m = re.search(r"(\d{4})(\d{2})(\d{2})", str(raw))
    if not m:
        return False
    try:
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return False
    return dt >= datetime.utcnow() - timedelta(days=days)


def content_md5(text: Optional[str]) -> str:
    if not text or len(text) < 50:
        return ""
    return hashlib.md5(text.encode("utf-8")).hexdigest()
