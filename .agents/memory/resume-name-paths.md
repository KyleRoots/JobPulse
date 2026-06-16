---
name: Resume vs inbound name-extraction paths
description: The two candidate-name extraction paths and the casing tradeoff of aligning them on the shared splitter.
---

# Two name-extraction paths must stay aligned

Candidate names are extracted in (at least) two independent places:

- **Inbound email** — `email_inbound_service` / `utils/candidate_name_extraction.py`
  (`split_full_name`, `is_valid_name`, `NAME_PARTICLES`). Already particle-aware.
- **Résumé header heuristic** — `resume_parser._parse_text` (first ~8 lines).

**Rule:** the résumé header path must use the shared `utils.candidate_name_extraction`
helpers, not its own casing/validation logic. Historically it had a private,
stricter gate that drifted out of sync.

**Why:** the résumé gate once required EVERY word to start uppercase
(`all(word[0].isupper() ...)`). Surnames with a lowercase particle ("Ahmed
el-Gabry", "Ludwig van Beethoven", "Robert de Niro") failed it, so the real
name line was skipped and the parser fell through to the next title-cased line
(e.g. a "Career Highlights" section header) → garbage like first="Career"
last="Highlights". The inbound path handled the same names fine — the bug was
purely the résumé path's divergence.

**How to apply:** when touching résumé name extraction, reuse `split_full_name`
+ `is_valid_name` and keep a particle-aware casing guard (allow lowercase
`NAME_PARTICLES` per hyphen segment; "el-Gabry" must be accepted). Add résumé
section-header words ("career", "highlights", "summary", ...) to the skip list
as defense-in-depth so a non-name line is never grabbed.

## Casing policy (shared `_titlecase`)

The shared `_titlecase` is the single normalizer for BOTH paths. Policy:
- **Preserve deliberate mixed-case**: a segment with an uppercase letter beyond
  position 0 AND a lowercase letter is kept as-is → `McDonald`, `MacLeod`,
  `DeVito`, `DiCaprio` preserved (NOT flattened).
- **Title-case signal-less input**: `JOHN SMITH`/`john smith` → `John Smith`;
  `el-Gabry` → `El-Gabry`.
- **Re-capitalize `Mc`** on title-cased output (unambiguous): `MCDONALD`/
  `mcdonald` → `McDonald`.
- **Do NOT auto-capitalize `Mac`** — collides with Macey/Mack/Machado/Macon, so
  guessing → false positives like `MacEy`. Signal-less `MACLEOD` stays
  `Macleod`; source-cased `MacLeod` preserved by the mixed-case rule.

**Why:** McDonald→Mcdonald was a real data-quality miss; `Mc` is safe to fix,
`Mac` is not (ambiguous without a dictionary).
**How to apply:** any Mc/Mac/casing tweak goes in the SHARED `_titlecase` so both
paths benefit — never re-fork the résumé path. Pinned by
`tests/test_resume_name_extraction.py` (`TestNameCasingPolicy`,
`TestLowercaseParticleSurnames`).
