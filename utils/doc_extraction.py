"""
Single source of truth for legacy .doc file text extraction.

Background
----------
The previous implementation called the `antiword` system binary at four
separate call sites (vetting/resume_utils.py, resume_parser.py,
scout_support/knowledge.py, scout_support/ai_analysis.py). On the
production container, antiword raises `OSError(errno=5, EIO)` for
every invocation — the binary's Nix store path resolves but the
underlying dependency chain is broken. Three of the four call sites
had no fallback, silently dropping legacy .doc content. The fourth
(vetting) only worked by accident via a generic exception catch that
fell through to AI vision OCR.

This module replaces antiword everywhere with a deterministic chain:

    1. If the bytes are actually a DOCX (PK magic) → python-docx
    2. If the bytes are actually a PDF (%PDF magic) → caller's PDF path
       (this module returns None and lets the caller route to its own
       PDF extractor — keeps PDF logic in one place)
    3. If the bytes are RTF → lightweight RTF stripper
    4. Otherwise (genuine OLE2 .doc or unknown binary) → returns None.
       Vision OCR is NOT used here: the OpenAI vision API only accepts
       image MIME types and rejects application/msword with a 400.
       Callers receive None and handle the clean failure themselves.

No system binaries are invoked. No filesystem temp files are created
unless the caller passes a path-style input.

Usage
-----
    from utils.doc_extraction import extract_doc_text

    text = extract_doc_text(file_bytes, filename="resume.doc")
    if not text:
        # Genuine extraction failure — caller decides how to surface it
        ...
"""

from __future__ import annotations

import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)


_OLE2_MAGIC = b"\xd0\xcf\x11\xe0"
_DOCX_MAGIC = b"PK"
_PDF_MAGIC = b"%PDF"
_RTF_MAGIC = b"{\\rtf"


def _detect_format(file_content: bytes) -> str:
    """Return one of: 'doc', 'docx', 'pdf', 'rtf', 'unknown'."""
    if not file_content or len(file_content) < 4:
        return "unknown"
    if file_content[:4] == _PDF_MAGIC:
        return "pdf"
    if file_content[:2] == _DOCX_MAGIC:
        return "docx"
    if file_content[:4] == _OLE2_MAGIC:
        return "doc"
    if file_content[:5] == _RTF_MAGIC:
        return "rtf"
    return "unknown"


def _try_python_docx(file_content: bytes) -> Optional[str]:
    """Last-chance attempt to read a .doc file as if it were .docx."""
    try:
        from docx import Document

        doc = Document(io.BytesIO(file_content))
        text = "\n".join(p.text for p in doc.paragraphs).strip()
        return text if text else None
    except Exception:
        return None


def _try_rtf(file_content: bytes) -> Optional[str]:
    """Lightweight RTF text extractor (no third-party dependency)."""
    try:
        text = file_content.decode("utf-8", errors="ignore")
        # Strip RTF control words and braces — good enough for plain content
        import re

        text = re.sub(r"\\[a-zA-Z]+-?\d*\s?", " ", text)
        text = re.sub(r"[{}]", "", text)
        text = re.sub(r"\\[*'\\]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text if len(text) > 20 else None
    except Exception:
        return None


def extract_doc_text(file_content: bytes, filename: str = "document.doc") -> Optional[str]:
    """
    Extract text from a legacy .doc (or RTF / mislabeled .docx) file.

    Order of attempts:
      1. If bytes are actually .docx (PK magic) → python-docx
      2. If bytes are actually RTF → lightweight RTF stripper
      3. If bytes are actually PDF → return None (caller routes to PDF extractor)
      4. Genuine OLE2 .doc or unknown binary → return None with a warning.
         Vision OCR is deliberately skipped: the OpenAI vision API only
         accepts image MIME types and rejects application/msword with a
         400 error.  There is no binary-free extraction path for genuine
         OLE2 .doc files in this environment.

    Returns the extracted text, or None if every attempt fails.
    """
    if not file_content:
        return None

    fmt = _detect_format(file_content)

    if fmt == "docx":
        logger.info(f"🔄 '{filename}' is labeled .doc but is actually a .docx — using python-docx")
        text = _try_python_docx(file_content)
        if text and len(text.strip()) > 10:
            return text
        logger.warning(f"⚠️ '{filename}' has DOCX magic bytes but python-docx could not extract text — returning None")
        return None

    if fmt == "rtf":
        logger.info(f"🔄 '{filename}' is RTF — using lightweight RTF stripper")
        text = _try_rtf(file_content)
        if text and len(text.strip()) > 10:
            return text
        logger.warning(f"⚠️ '{filename}' is RTF but stripper produced no usable text — returning None")
        return None

    if fmt == "pdf":
        logger.info(f"🔄 '{filename}' is labeled .doc but is actually a PDF — caller should route to PDF extractor")
        return None

    # Genuine OLE2 .doc or unrecognised binary format.
    # Vision OCR cannot process non-image MIME types (returns HTTP 400).
    # No system-binary extraction is available in this environment.
    fmt_label = "OLE2 .doc" if fmt == "doc" else "unknown binary"
    logger.warning(
        f"⚠️ '{filename}' is a genuine {fmt_label} file — no text extraction "
        f"available without system binaries (antiword/LibreOffice). Returning None."
    )
    return None
