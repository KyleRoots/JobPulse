"""Retired feed reference numbers still resolve, and new numbers do not reuse them."""
from datetime import datetime

from lightweight_reference_refresh import save_references_to_database
from models.scheduling import JobReferenceAlias, JobReferenceNumber
from reference_history import allocate_reference, bullhorn_job_id_for_reference


_XML = """<?xml version="1.0"?>
<source>
  <job>
    <bhatsid>46200</bhatsid>
    <referencenumber>NEWREF0001</referencenumber>
    <title>Picker</title>
  </job>
</source>
"""


def test_replacing_a_reference_keeps_the_old_number(app):
    with app.app_context():
        from extensions import db

        db.session.add(JobReferenceNumber(
            bullhorn_job_id="46200",
            reference_number="OLDREF0001",
            job_title="Picker",
        ))
        db.session.commit()

        assert save_references_to_database(_XML) is True
        current = JobReferenceNumber.query.filter_by(bullhorn_job_id="46200").one()
        assert current.reference_number == "NEWREF0001"
        retired = JobReferenceAlias.query.filter_by(reference_number="OLDREF0001").one()
        assert retired.bullhorn_job_id == "46200"
        assert bullhorn_job_id_for_reference("OLDREF0001") == "46200"
        assert bullhorn_job_id_for_reference("NEWREF0001") == "46200"

        assert save_references_to_database(_XML) is True
        assert JobReferenceAlias.query.filter_by(reference_number="OLDREF0001").count() == 1


def test_new_reference_skips_a_retired_number(app):
    with app.app_context():
        from extensions import db

        db.session.add(JobReferenceAlias(
            reference_number="TAKEN12345",
            bullhorn_job_id="46200",
            retired_at=datetime.utcnow(),
        ))
        db.session.commit()
        calls = {"n": 0}

        def _generate():
            calls["n"] += 1
            return "TAKEN12345" if calls["n"] == 1 else "FRESH12345"

        assert allocate_reference(set(), _generate) == "FRESH12345"
        assert calls["n"] == 2
