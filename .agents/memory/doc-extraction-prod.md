---
name: Legacy .doc extraction in production
description: Why .doc resume extraction must not depend on the antiword system binary, and how the pure-Python fallback works.
---

# Legacy OLE2 `.doc` extraction must stay pure-Python

**Rule:** Genuine OLE2 `.doc` text extraction must go through the in-process
`olefile`-based parser in `utils/doc_extraction.py` (`_try_olefile_doc`), NOT the
`antiword` system binary. olefile runs first; antiword is only a secondary fallback.

**Why:** The `antiword` Nix binary works in dev but reliably fails in the
production runtime with `OSError errno=5 (EIO)` even after a fresh publish — a
deployment-runtime/Nix-store issue, not a missing library or code bug. Relying on
it silently dropped all `.doc` resume coverage in prod (lost coverage, never an
outage, because the chain is fail-soft).

**How to apply:**
- Never reintroduce antiword (or any system binary) as the *primary* `.doc` path.
  A regression test (`test_no_antiword_calls_in_resume_extraction_paths`) guards
  the four historical call sites; keep it green.
- The parser reads the WordDocument stream → FIB offsets (wIdent 0xA5EC, table
  selector flag bit 0x0200, ccpText@0x4C, fcClx@0x01A2, lcbClx@0x01A6) → CLX piece
  table; decodes compressed cp1252 vs UTF-16LE per piece; trims to ccpText.
- A printable-ratio guard (≥0.80) means malformed/binary input returns `None`
  rather than polluting candidate records — extraction is advisory and must never
  raise to callers.
- Tests are fixture-backed: real OLE2 files live in `tests/fixtures/` (Apache POI
  test-data, Apache-2.0). Any new edge case should add a real fixture, not synthetic
  bytes, since the parser depends on true OLE2 structure.
