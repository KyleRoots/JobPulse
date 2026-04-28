"""
Regression tests for utils.doc_extraction — the antiword replacement.

These tests lock in the behaviour we shipped to fix the production
[Errno 5] EIO cascade where antiword's broken Nix store path was
silently dropping legacy .doc resumes from screening, job application,
and scout_support pipelines.

What we verify:
  - Magic-byte routing chooses the right extractor regardless of
    filename extension.
  - DOCX bytes mislabelled as .doc are read by python-docx.
  - PDF bytes mislabelled as .doc return None so callers route to
    their own PDF extractor (avoids duplicating PDF logic).
  - RTF bytes mislabelled as .doc are stripped without invoking the
    network OCR path.
  - Genuine OLE2 .doc bytes return None cleanly — vision OCR is
    deliberately skipped because the OpenAI vision API rejects
    non-image MIME types (application/msword) with HTTP 400.
  - The four legacy antiword call sites no longer reference 'antiword'
    in source — defence in depth against accidental reintroduction.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from utils.doc_extraction import _detect_format, extract_doc_text


# ---------------------------------------------------------------------------
# Magic-byte detection
# ---------------------------------------------------------------------------

def test_detect_format_pdf():
    assert _detect_format(b"%PDF-1.4\n...") == "pdf"


def test_detect_format_docx():
    assert _detect_format(b"PK\x03\x04...") == "docx"


def test_detect_format_doc_ole2():
    assert _detect_format(b"\xd0\xcf\x11\xe0\xa1\xb1...") == "doc"


def test_detect_format_rtf():
    assert _detect_format(b"{\\rtf1\\ansi...") == "rtf"


def test_detect_format_unknown():
    assert _detect_format(b"Hello plain text") == "unknown"


def test_detect_format_empty():
    assert _detect_format(b"") == "unknown"
    assert _detect_format(None) == "unknown"  # type: ignore[arg-type]
    assert _detect_format(b"abc") == "unknown"


# ---------------------------------------------------------------------------
# extract_doc_text routing
# ---------------------------------------------------------------------------

def test_pdf_bytes_mislabelled_as_doc_returns_none():
    """PDF bytes labelled .doc should return None — caller routes to PDF extractor."""
    pdf_bytes = b"%PDF-1.4\n%FAKE-PDF-FOR-TESTING\n"
    assert extract_doc_text(pdf_bytes, "resume.doc") is None


def test_docx_bytes_mislabelled_as_doc_uses_python_docx():
    """DOCX bytes labelled .doc should be read by python-docx."""
    from docx import Document

    docx_doc = Document()
    docx_doc.add_paragraph("Hello from a docx mislabelled as doc")
    docx_doc.add_paragraph("Second paragraph with more content here")
    buf = io.BytesIO()
    docx_doc.save(buf)
    docx_bytes = buf.getvalue()

    # Confirm magic bytes look like docx (PK)
    assert docx_bytes[:2] == b"PK"

    text = extract_doc_text(docx_bytes, "resume.doc")
    assert text is not None
    assert "Hello from a docx" in text
    assert "Second paragraph" in text


def test_rtf_bytes_extracted_without_network_call():
    """RTF bytes should be stripped locally, never hitting OpenAI."""
    rtf = (
        b"{\\rtf1\\ansi\\deff0 {\\fonttbl {\\f0 Times;}}"
        b"\\f0 The candidate has 10 years of Python experience.}"
    )
    with patch("utils.doc_extraction._try_vision_ocr") as mock_ocr:
        text = extract_doc_text(rtf, "resume.doc")
        mock_ocr.assert_not_called()

    assert text is not None
    assert "candidate" in text.lower()
    assert "python" in text.lower()


def test_genuine_ole2_doc_returns_none_without_vision_ocr():
    """A real .doc (OLE2 magic) must return None cleanly — vision OCR must
    NOT be called because the OpenAI vision API rejects application/msword
    with HTTP 400 ('Invalid MIME type. Only image types are supported.')."""
    ole2_bytes = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 200

    with patch("utils.doc_extraction._try_vision_ocr") as mock_ocr:
        text = extract_doc_text(ole2_bytes, "resume.doc")
        mock_ocr.assert_not_called()

    assert text is None


def test_empty_bytes_return_none():
    assert extract_doc_text(b"", "resume.doc") is None
    assert extract_doc_text(None, "resume.doc") is None  # type: ignore[arg-type]


def test_ole2_doc_returns_none_without_network_call():
    """Genuine OLE2 .doc must return None without touching the network.
    Vision OCR is disabled for this format — verify no OpenAI call is made."""
    ole2_bytes = b"\xd0\xcf\x11\xe0" + b"\x00" * 100

    with patch("utils.doc_extraction._try_vision_ocr") as mock_ocr:
        text = extract_doc_text(ole2_bytes, "resume.doc")
        mock_ocr.assert_not_called()

    assert text is None


def test_unknown_binary_returns_none_without_network_call():
    """Unrecognised binary format must return None without touching the network."""
    unknown_bytes = b"\x00\x01\x02\x03" + b"\xff" * 100

    with patch("utils.doc_extraction._try_vision_ocr") as mock_ocr:
        text = extract_doc_text(unknown_bytes, "mystery.doc")
        mock_ocr.assert_not_called()

    assert text is None


# ---------------------------------------------------------------------------
# Defence-in-depth: antiword must stay out of the codebase
# ---------------------------------------------------------------------------

def test_no_antiword_calls_in_resume_extraction_paths():
    """Regression guard: the four call sites we cleaned must not reinvoke antiword.

    If a future change reintroduces a subprocess call to antiword anywhere
    in these files, this test fails loudly so the broken-Nix-binary bug
    cannot return silently.
    """
    import re
    from pathlib import Path

    suspect_files = [
        Path("vetting/resume_utils.py"),
        Path("resume_parser.py"),
        Path("scout_support/knowledge.py"),
        Path("scout_support/ai_analysis.py"),
    ]

    # Only flag actual code invocations, not docstring mentions of the
    # historical context. We look for the string literal 'antiword' as
    # used in subprocess calls (subprocess.run(['antiword', ...]) or
    # shutil.which('antiword')) — those are the real reintroductions.
    pattern = re.compile(r"""['"]antiword['"]""")
    offenders = []
    for f in suspect_files:
        if not f.exists():
            continue
        content = f.read_text(encoding="utf-8")
        if pattern.search(content):
            offenders.append(str(f))

    assert not offenders, (
        f"antiword reference reintroduced in: {offenders}. "
        "Use utils.doc_extraction.extract_doc_text instead — antiword "
        "fails with [Errno 5] EIO on the production Nix store path."
    )
