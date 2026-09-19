"""Tests for SCOUT_TENANT feed selection (Myticas/STSI vs Qualified)."""

from feeds.feed_config import (
    CHANNEL_FEEDS,
    QUALIFIED_V2_FILENAME,
    V2_FILENAME,
    V2_TEARSHEET_IDS,
    all_xml_feed_tearsheet_ids,
    channel_feeds_for_upload,
    feeds_configured_for_tenant,
    get_channel_feeds,
    get_default_apply_email,
    get_default_apply_host,
    get_v2_filenames,
    get_v2_publisher,
    get_v2_tearsheet_ids,
    indeed_native_publish_enabled,
    is_qualified_tenant,
)


class TestScoutTenantFeedSelector:
    def test_default_tenant_is_myticas(self, monkeypatch):
        monkeypatch.delenv('SCOUT_TENANT', raising=False)
        assert is_qualified_tenant() is False
        assert get_v2_tearsheet_ids() == list(V2_TEARSHEET_IDS)
        assert get_v2_filenames()[0] == V2_FILENAME
        assert get_v2_publisher()[0] == 'Myticas Consulting'
        assert get_default_apply_host() == 'apply.myticas.com'
        assert get_default_apply_email() == 'apply@myticas.com'
        assert feeds_configured_for_tenant() is True
        keys = {f['key'] for f in get_channel_feeds()}
        assert keys == {'stsi_indeed', 'stsi_ziprecruiter'}

    def test_qualified_tenant_isolates_feeds(self, monkeypatch):
        monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
        assert is_qualified_tenant() is True
        assert get_v2_tearsheet_ids() == [4]
        assert all_xml_feed_tearsheet_ids() == [4, 2, 3]
        assert feeds_configured_for_tenant() is True
        assert get_v2_filenames()[0] == QUALIFIED_V2_FILENAME
        assert get_v2_publisher() == ('Qualified Staffing', 'https://www.q-staffing.com')
        assert get_default_apply_host() == 'qualified.scoutgenius.ai'
        assert get_default_apply_email() == 'apply@q-staffing.com'
        keys = {f['key'] for f in get_channel_feeds()}
        assert keys == {'qualified_indeed', 'qualified_ziprecruiter'}
        # Must not leak Myticas/STSI channel keys or IDs
        assert 'stsi_indeed' not in keys
        by_key = {cfg['key']: cfg['tearsheet_ids'] for cfg in get_channel_feeds()}
        assert by_key['qualified_indeed'] == [2]
        assert by_key['qualified_ziprecruiter'] == [3]
        assert 1531 not in all_xml_feed_tearsheet_ids()
        assert 1640 not in all_xml_feed_tearsheet_ids()

    def test_qualified_forces_indeed_native_off(self, monkeypatch):
        monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
        monkeypatch.setenv('INDEED_TEARSHEET_PUBLISH_ENABLED', 'true')
        assert indeed_native_publish_enabled() is False
        for cfg in channel_feeds_for_upload():
            assert not cfg.get('force_empty')

    def test_myticas_constants_unchanged_for_legacy_imports(self):
        # Module-level constants remain the Myticas/STSI set for existing tests.
        assert 1531 in V2_TEARSHEET_IDS
        assert CHANNEL_FEEDS[0]['key'] == 'stsi_indeed'
