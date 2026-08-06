"""Tests for LinkedIn seat / recruiter mapping seed (seat-report.csv sync)."""

from seeding.settings import seed_recruiter_mappings


EXPECTED_TAGS = {
    'Reena Setya': '#LI-RS1',
    'Myticas Recruiter': '#LI-RS1',
    'Rachel Johnson': '#LI-RM1',
    'Rachel Mann': '#LI-RM1',
    'Lisa Keirsted': '#LI-LM1',
    'Lisa Mattis-Keirsted': '#LI-LM1',
    'Jasmine Harvey': '#LI-JH1',
    'Doug Billot': '#LI-DB1',
    'Anna Wujciak-Flynn': '#LI-AW1',
    'Daniel Sifer': '#LI-DS1',
    'Dan Sifer': '#LI-DS1',
}


REMOVED_NAMES = {
    'Amanda Messina',
    'Austin Zachrich',
    'Chris Halkai',
    'Dean Theodossiou',
    'Innocent Nangoma',
    'Jayne Kritschgau',
    'Kaniz Abedin',
    'Maddie Lewis',
    'Michael Billiu',
    'Michelle Corino',
    'Mike Gebara',
    'Nick Theodossiou',
    'Ryan Green',
    'Sam Osman',
}


class TestSeedRecruiterMappings:
    def test_priority_fixes_and_csv_sync(self, app):
        from app import db
        from models import RecruiterMapping

        with app.app_context():
            RecruiterMapping.query.delete()
            db.session.commit()

            # Seed stale/wrong rows that the cleanup must correct or remove.
            db.session.add(RecruiterMapping(recruiter_name='Reena Setya', linkedin_tag='#LI-RS2'))
            db.session.add(RecruiterMapping(recruiter_name='Rachel Johnson', linkedin_tag='#LI-RJ1'))
            db.session.add(RecruiterMapping(recruiter_name='Lisa Keirsted', linkedin_tag='#LI-DS1'))
            db.session.add(RecruiterMapping(recruiter_name='Amanda Messina', linkedin_tag='#LI-AM1'))
            db.session.add(RecruiterMapping(recruiter_name='Myticas Recruiter', linkedin_tag='#LI-RS1'))
            db.session.commit()

            seed_recruiter_mappings(db, RecruiterMapping)

            for name, tag in EXPECTED_TAGS.items():
                row = RecruiterMapping.query.filter_by(recruiter_name=name).first()
                assert row is not None, f'missing mapping for {name}'
                assert row.linkedin_tag == tag, f'{name}: expected {tag}, got {row.linkedin_tag}'

            for name in REMOVED_NAMES:
                assert (
                    RecruiterMapping.query.filter_by(recruiter_name=name).first() is None
                ), f'obsolete mapping still present: {name}'

            assert RecruiterMapping.query.filter_by(linkedin_tag='#LI-RJ1').count() == 0
            assert RecruiterMapping.query.filter_by(linkedin_tag='#LI-RS2').count() == 0

            lisa = RecruiterMapping.query.filter_by(recruiter_name='Lisa Keirsted').first()
            assert lisa.linkedin_tag != '#LI-DS1'

    def test_idempotent_second_pass(self, app):
        from app import db
        from models import RecruiterMapping

        with app.app_context():
            RecruiterMapping.query.delete()
            db.session.commit()

            seed_recruiter_mappings(db, RecruiterMapping)
            count_1 = RecruiterMapping.query.count()
            seed_recruiter_mappings(db, RecruiterMapping)
            count_2 = RecruiterMapping.query.count()
            assert count_1 == count_2
            assert count_1 >= len(EXPECTED_TAGS)
