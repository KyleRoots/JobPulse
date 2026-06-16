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

## Casing tradeoff (intentional)

Routing through `split_full_name` title-cases output:
- `JOHN SMITH` → `John Smith` (improvement)
- `el-Gabry` → `El-Gabry` (improvement, matches desired Bullhorn form)
- `McDonald`/`MacLeod`/`DeVito` → `Mcdonald`/`Macleod`/`Devito` (the one downside)

Internal-cap flattening is accepted for cross-path consistency. If a future ask
wants Mc/Mac preserved, fix it in the SHARED `_titlecase` so BOTH paths benefit
(don't re-fork the résumé path). Pinned by `tests/test_resume_name_extraction.py`
(`TestLowercaseParticleSurnames`).
