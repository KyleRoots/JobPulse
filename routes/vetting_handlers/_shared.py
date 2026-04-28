"""Shared helpers for routes/vetting_handlers/* modules."""


def get_db():
    """Get database instance from app context.

    Mirrors the original helper that lived in routes/vetting.py. Kept as a
    function (rather than a top-level import) so the active SQLAlchemy
    instance is resolved lazily at request time, matching the prior behavior.
    """
    from app import db
    return db
