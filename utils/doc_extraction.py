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
    3. Otherwise (genuine OLE2 .doc, RTF, or unknown) → AI vision OCR
       via the existing GPT-4.1-mini path

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

import base64
import io
import logging
import os
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


def _try_vision_ocr(file_content: bytes, filename: str) -> Optional[str]:
    """Use GPT-4.1-mini vision OCR to read the .doc file as a document image."""
    try:
        from openai import OpenAI

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set — cannot use vision OCR fallback")
            return None

        client = OpenAI(api_key=api_key)
        b64 = base64.b64encode(file_content).decode("utf-8")
        ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else "doc"
        mime = {
            "doc": "application/msword",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "rtf": "application/rtf",
        }.get(ext, "application/octet-stream")

        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an OCR assistant. Extract ALL text from the document provided. "
                        "Reproduce the text exactly as it appears — preserve names, dates, job titles, "
                        "skills, phone numbers, emails, addresses, and formatting structure. "
                        "Do not summarize or interpret. Output only the extracted text, nothing else."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract all text from this resume document:"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{b64}",
                                "detail": "high",
                            },
                        },
                    ],
                },
            ],
            max_completion_tokens=4000,
        )

        if not response.choices:
            return None
        text = (response.choices[0].message.content or "").strip()
        if text:
            logger.info(
                f"📸 Vision OCR extracted {len(text)} chars from .doc-style file '{filename}'"
            )
        return text or None
    except Exception as e:
        logger.error(f"Vision OCR failed for '{filename}': {e}")
        return None


def extract_doc_text(file_content: bytes, filename: str = "document.doc") -> Optional[str]:
    """
    Extract text from a legacy .doc (or RTF / mislabeled .docx) file.

    Order of attempts:
      1. If bytes are actually .docx (PK magic) → python-docx
      2. If bytes are actually RTF → lightweight RTF stripper
      3. Otherwise → AI vision OCR (handles real OLE2 .doc files)

    Returns the extracted text, or None if every attempt fails. PDF
    bytes return None — caller is expected to route PDFs to their own
    extractor (this module intentionally does not duplicate PDF logic).
    """
    if not file_content:
        return None

    fmt = _detect_format(file_content)

    if fmt == "docx":
        logger.info(f"🔄 '{filename}' is labeled .doc but is actually a .docx — using python-docx")
        text = _try_python_docx(file_content)
        if text and len(text.strip()) > 10:
            return text
        # Fall through to OCR

    if fmt == "rtf":
        logger.info(f"🔄 '{filename}' is RTF — using lightweight RTF stripper")
        text = _try_rtf(file_content)
        if text and len(text.strip()) > 10:
            return text
        # Fall through to OCR

    if fmt == "pdf":
        # Caller should handle PDFs — return None so they route correctly
        logger.info(f"🔄 '{filename}' is labeled .doc but is actually a PDF — caller should route to PDF extractor")
        return None

    # Genuine .doc (OLE2) or unknown format → vision OCR
    return _try_vision_ocr(file_content, filename)
