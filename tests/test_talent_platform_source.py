"""Blank source on Talent Platform-owned candidates → Corporate Website."""


def test_blank_source_gets_corporate_website():
    from tasks.talent_platform_source import source_update_payload

    assert source_update_payload({'id': 1, 'source': ''}) == {
        'source': 'Corporate Website',
    }
    assert source_update_payload({'id': 1, 'source': '   '}) == {
        'source': 'Corporate Website',
    }
    assert source_update_payload({'id': 1}) == {'source': 'Corporate Website'}


def test_existing_source_is_not_overwritten():
    from tasks.talent_platform_source import source_update_payload

    assert source_update_payload({'id': 1, 'source': 'ZipRecruiter Job Board'}) is None
    assert source_update_payload({'id': 1, 'source': 'Indeed Job Board'}) is None


def test_skips_when_not_qualified(monkeypatch):
    monkeypatch.delenv('SCOUT_TENANT', raising=False)
    from tasks.talent_platform_source import backfill_talent_platform_source

    summary = backfill_talent_platform_source()
    assert summary['enabled'] is False
    assert 'not Qualified' in summary['message']
