"""Vetting handler submodules.

Importing this package (or any of its submodules) registers route handlers on
the shared ``vetting_bp`` blueprint defined in ``routes/vetting.py``. Submodules
deliberately have no public exports — they exist only for their import-time
side effect of calling ``@vetting_bp.route(...)``.

Layout (see also routes/vetting.py):
    _shared          — get_db() helper used by every other handler module
    settings         — vetting settings page + save + manual health-check
    dispatch         — manual run / rescreen / re-vet / clean-slate operations
    diagnostics      — diagnostic JSON endpoints + activity monitor + lock/note ops
    email            — test notification emails
    job_requirements — per-job custom requirements / threshold / AI refresh
    embedding_audit  — embedding-filter audit page, CSV exports, retry controls
"""
