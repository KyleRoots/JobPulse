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
       a. Try the pure-Python ``olefile`` parser first.  It reads the
          WordDocument stream, parses the FIB + piece table, and
          reconstructs the main body text.  Crucially it behaves
          identically in dev and production, unlike antiword whose
          Nix binary EIOs in the production runtime.  A
          printable-ratio guard ensures binary garbage is never
          returned.
       b. If olefile produces no usable text → fall back to the
          antiword subprocess (kept only as a safety net).  All of
          antiword's failure modes (EIO, missing binary, timeout,
          non-zero exit) are caught and logged; it never raises.
       c. If neither extractor succeeds → return None.
          Vision OCR is NOT attempted here: the OpenAI vision API
          only accepts image MIME types and rejects
          application/msword with HTTP 400.  Callers receive None
          and surface the failure themselves.

No filesystem temp files persist after this module returns (the
antiword fallback path creates a temp file and deletes it
immediately; the olefile path is fully in-memory).

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
import re
import struct
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


# Word field-character markers (MS-DOC): field instruction code runs between
# 0x13 (begin) and 0x14 (separator); the visible result runs from 0x14 to
# 0x15 (end). We drop the instruction code and keep the visible result.
_FIELD_BEGIN = "\x13"
_FIELD_SEP = "\x14"
_FIELD_END = "\x15"


def _printable_ratio(text: str) -> float:
    """Fraction of characters that are printable/whitespace (garbage guard)."""
    if not text:
        return 0.0
    ok = sum(1 for c in text if c.isprintable() or c in "\n\r\t ")
    return ok / len(text)


def _clean_doc_text(raw: str) -> str:
    """Normalise raw Word character runs into readable text.

    Translates Word control characters (paragraph/line/page breaks, cell
    marks, non-breaking hyphen/space) and strips field instruction codes,
    embedded-object placeholders, and other binary control bytes.
    """
    out = []
    field_depth = 0  # >0 while inside a field instruction-code run (0x13..0x14)
    for ch in raw:
        o = ord(ch)
        if ch == _FIELD_BEGIN:
            field_depth += 1
            continue
        if ch == _FIELD_SEP:
            if field_depth > 0:
                field_depth -= 1
            continue
        if ch == _FIELD_END:
            continue
        if field_depth > 0:
            continue  # skip field instruction text (e.g. HYPERLINK codes)
        if o in (0x0D, 0x0B, 0x0C):  # paragraph end / line break / page break
            out.append("\n")
        elif o in (0x07, 0x09):      # cell/row mark, tab
            out.append("\t")
        elif o == 0x0A:              # explicit newline
            out.append("\n")
        elif o == 0x1E:              # non-breaking hyphen
            out.append("-")
        elif o == 0xA0:              # non-breaking space
            out.append(" ")
        elif o == 0x1F:              # optional hyphen
            continue
        elif o in (0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x08):
            continue                 # object / footnote / annotation placeholders
        elif o < 0x20:               # any other control byte
            continue
        else:
            out.append(ch)
    text = "".join(out)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _parse_piece_table(clx: bytes):
    """Parse the CLX (complex part) to return the document's piece table.

    Returns a list of (cpStart, cpEnd, fc, fCompressed) tuples, or [] if the
    CLX has no piece table. ``fc`` is the byte offset of the piece's text
    inside the WordDocument stream; ``fCompressed`` indicates 8-bit (cp1252)
    rather than 16-bit (UTF-16LE) characters.
    """
    i = 0
    n = len(clx)
    pcdt = None
    while i < n:
        clxt = clx[i]
        if clxt == 1:  # Prc — grpprl run to skip
            if i + 3 > n:
                break
            cb = struct.unpack_from("<H", clx, i + 1)[0]
            i += 3 + cb
        elif clxt == 2:  # Pcdt — the piece table
            if i + 5 > n:
                break
            lcb = struct.unpack_from("<I", clx, i + 1)[0]
            pcdt = clx[i + 5:i + 5 + lcb]
            break
        else:
            break
    if not pcdt:
        return []
    lcb = len(pcdt)
    # PlcPcd = (k+1) CPs (4 bytes) followed by k PCDs (8 bytes) => 12k + 4
    if lcb < 16 or (lcb - 4) % 12 != 0:
        return []  # malformed / truncated piece table
    k = (lcb - 4) // 12
    if k <= 0:
        return []
    cps = [struct.unpack_from("<I", pcdt, j * 4)[0] for j in range(k + 1)]
    pieces = []
    base = 4 * (k + 1)
    for j in range(k):
        pcd = pcdt[base + j * 8: base + j * 8 + 8]
        fc_field = struct.unpack_from("<I", pcd, 2)[0]
        compressed = (fc_field & 0x40000000) != 0
        fc = fc_field & 0x3FFFFFFF
        if compressed:
            fc //= 2
        pieces.append((cps[j], cps[j + 1], fc, compressed))
    return pieces


def _try_olefile_doc(file_content: bytes, filename: str = "document.doc") -> Optional[str]:
    """Pure-Python OLE2 .doc text extractor (no system-binary dependency).

    Reads the ``WordDocument`` stream, parses the FIB and piece table, and
    reconstructs the main document text. Works identically in dev and
    production (unlike antiword, whose Nix binary EIOs in production).

    Returns the extracted text, or None on any failure / non-text content.
    A printable-ratio guard ensures binary garbage is never returned.
    """
    try:
        import olefile
    except ImportError:
        logger.warning(
            "olefile not installed — cannot parse OLE2 .doc in pure Python"
        )
        return None

    ole = None
    try:
        ole = olefile.OleFileIO(io.BytesIO(file_content))
        if not ole.exists("WordDocument"):
            return None
        wd = ole.openstream("WordDocument").read()
        if len(wd) < 0x200:
            return None
        if struct.unpack_from("<H", wd, 0x00)[0] != 0xA5EC:  # FIB wIdent
            return None

        flags = struct.unpack_from("<H", wd, 0x0A)[0]
        table_name = "1Table" if (flags & 0x0200) else "0Table"
        ccp_text = struct.unpack_from("<i", wd, 0x4C)[0]
        fc_clx = struct.unpack_from("<I", wd, 0x01A2)[0]
        lcb_clx = struct.unpack_from("<I", wd, 0x01A6)[0]

        if not ole.exists(table_name):
            alt = "0Table" if table_name == "1Table" else "1Table"
            table_name = alt if ole.exists(alt) else None
        if not table_name or lcb_clx == 0:
            return None  # no piece table — bail rather than risk garbage

        tbl = ole.openstream(table_name).read()
        clx = tbl[fc_clx:fc_clx + lcb_clx]
        pieces = _parse_piece_table(clx)
        if not pieces:
            return None

        chars = []
        for cp_start, cp_end, fc, compressed in pieces:
            count = cp_end - cp_start
            if count <= 0:
                continue
            if compressed:
                chars.append(wd[fc:fc + count].decode("cp1252", errors="replace"))
            else:
                chars.append(wd[fc:fc + count * 2].decode("utf-16-le", errors="replace"))
        text = "".join(chars)
        # Trim to the main document body (CPs beyond ccpText are footnotes,
        # headers, etc.) when the count is sane.
        if 0 < ccp_text <= len(text):
            text = text[:ccp_text]

        cleaned = _clean_doc_text(text)
        if cleaned and len(cleaned) > 10 and _printable_ratio(cleaned) >= 0.80:
            logger.info(
                f"✅ olefile extracted {len(cleaned)} chars from '{filename}'"
            )
            return cleaned
        return None
    except Exception as exc:
        logger.debug(f"olefile .doc parse failed for '{filename}': {exc}")
        return None
    finally:
        if ole is not None:
            try:
                ole.close()
            except Exception:
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
    # Prefer the pure-Python olefile parser: it behaves identically in dev and
    # production, whereas the antiword system binary EIOs in the production
    # runtime. antiword remains only as a secondary fallback.
    fmt_label = "OLE2 .doc" if fmt == "doc" else "unknown binary"
    logger.info(
        f"🔄 '{filename}' is a genuine {fmt_label} — extracting via pure-Python olefile"
    )

    text = _try_olefile_doc(file_content, filename)
    if text and len(text.strip()) > 10:
        return text

    logger.info(
        f"🔄 olefile produced no usable text for '{filename}' — trying antiword fallback"
    )
    text = _try_antiword(file_content, filename)
    if text and len(text.strip()) > 10:
        return text

    logger.warning(
        f"⚠️ '{filename}' is a genuine {fmt_label} — no extractor produced usable "
        f"text. Returning None."
    )
    return None
