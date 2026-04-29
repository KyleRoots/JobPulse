"""
Single source of truth for legacy .doc file text extraction.

Background
----------
The previous implementation called the `antiword` system binary at four
separate call sites (vetting/resume_utils.py, resume_parser.py,
scout_support/knowledge.py, scout_support/ai_analysis.py). On a prior
production deployment the binary raised ``OSError(errno=5, EIO)`` for
every invocation — the Nix store path resolved but the underlying
dependency chain was broken. Those four call sites were cleaned up and
antiword use was centralised here so that a single graceful fallback
path exists and future failures cannot silently cascade.

This module uses a deterministic extraction chain:

    1. If the bytes are actually a DOCX (PK magic) → python-docx
    2. If the bytes are actually a PDF (%PDF magic) → return None so
       the caller can route to its own PDF extractor (avoids
       duplicating PDF logic).
    3. If the bytes are RTF → lightweight RTF stripper
    4. Genuine OLE2 .doc or unknown binary →
       a. Try antiword subprocess.  All failure modes (EIO, missing
          binary, timeout, non-zero exit) are caught and logged;
          the function never raises.
       b. If antiword is unavailable or fails → return None.
          Vision OCR is NOT attempted here: the OpenAI vision API
          only accepts image MIME types and rejects
          application/msword with HTTP 400.  Callers receive None
          and surface the failure themselves.

No filesystem temp files persist after this module returns (the
antiword path creates a temp file and deletes it immediately).

Usage
-----
    from utils.doc_extraction import extract_doc_text

    text = extract_doc_text(file_bytes, filename="resume.doc")
    if not text:
        # Extraction failed — caller decides how to surface it
        ...
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import tempfile
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
        import re

        text = re.sub(r"\\[a-zA-Z]+-?\d*\s?", " ", text)
        text = re.sub(r"[{}]", "", text)
        text = re.sub(r"\\[*'\\]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text if len(text) > 20 else None
    except Exception:
        return None


def _try_antiword(file_content: bytes, filename: str = "document.doc") -> Optional[str]:
    """Attempt text extraction using the antiword system binary.

    Writes ``file_content`` to a temp file, runs ``antiword -w 0``, and
    returns the stdout as a string.  The temp file is always deleted.

    Handles every failure mode gracefully:
    - ``FileNotFoundError``  — antiword not on PATH
    - ``OSError(errno=5)``   — EIO from a broken Nix store path
    - ``TimeoutExpired``     — binary hung
    - Non-zero exit code     — antiword could not parse the file
    - Any other exception    — logged at DEBUG, returns None

    Returns the extracted text (non-empty string) or None.
    """
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".doc") as tmp:
            tmp.write(file_content)
            tmp_path = tmp.name

        result = subprocess.run(
            ["antiword", "-w", "0", tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout.strip()
            logger.info(
                f"✅ antiword extracted {len(text)} chars from '{filename}'"
            )
            return text

        logger.debug(
            f"antiword rc={result.returncode} for '{filename}': "
            f"{result.stderr[:120]}"
        )
        return None

    except FileNotFoundError:
        logger.warning("antiword binary not found — skipping OLE2 .doc extraction")
        return None
    except OSError as exc:
        import errno as _errno
        if exc.errno == _errno.EIO:
            logger.warning(
                f"antiword EIO error for '{filename}' — Nix binary broken, "
                f"returning None"
            )
        else:
            logger.warning(f"antiword OSError for '{filename}': {exc}")
        return None
    except subprocess.TimeoutExpired:
        logger.warning(f"antiword timed out processing '{filename}'")
        return None
    except Exception as exc:
        logger.debug(f"antiword unexpected failure for '{filename}': {exc}")
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def extract_doc_text(file_content: bytes, filename: str = "document.doc") -> Optional[str]:
    """
    Extract text from a legacy .doc (or RTF / mislabeled .docx) file.

    Order of attempts:
      1. If bytes are actually .docx (PK magic) → python-docx
      2. If bytes are actually RTF → lightweight RTF stripper
      3. If bytes are actually PDF → return None (caller routes to PDF extractor)
      4. Genuine OLE2 .doc or unknown binary →
         a. antiword subprocess (graceful on EIO / missing binary)
         b. None if antiword unavailable or fails

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
        logger.warning(
            f"⚠️ '{filename}' has DOCX magic bytes but python-docx could not "
            f"extract text — returning None"
        )
        return None

    if fmt == "rtf":
        logger.info(f"🔄 '{filename}' is RTF — using lightweight RTF stripper")
        text = _try_rtf(file_content)
        if text and len(text.strip()) > 10:
            return text
        logger.warning(
            f"⚠️ '{filename}' is RTF but stripper produced no usable text — "
            f"returning None"
        )
        return None

    if fmt == "pdf":
        logger.info(
            f"🔄 '{filename}' is labeled .doc but is actually a PDF — "
            f"caller should route to PDF extractor"
        )
        return None

    # Genuine OLE2 .doc or unrecognised binary format.
    # Try antiword first; fall back to None on any failure.
    fmt_label = "OLE2 .doc" if fmt == "doc" else "unknown binary"
    logger.info(
        f"🔄 '{filename}' is a genuine {fmt_label} — attempting antiword extraction"
    )

    text = _try_antiword(file_content, filename)
    if text and len(text.strip()) > 10:
        return text

    logger.warning(
        f"⚠️ '{filename}' is a genuine {fmt_label} — antiword unavailable or "
        f"returned no usable text. Returning None."
    )
    return None
