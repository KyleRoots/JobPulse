"""120h / manual reference refresh must cover v2 + STSI channel tearsheets."""
from unittest.mock import MagicMock, patch

from feeds.feed_config import (
    TEARSHEET_STSI_INDEED,
    TEARSHEET_STSI_ZIPRECRUITER,
    V2_TEARSHEET_IDS,
    all_xml_feed_tearsheet_ids,
)


def test_refresh_all_feed_references_uses_all_tearsheets():
    from lightweight_reference_refresh import refresh_all_feed_references

    generator = MagicMock()
    generator.generate_fresh_xml.return_value = (
        '<?xml version="1.0"?><source></source>',
        {'job_count': 3, 'xml_size_bytes': 40, 'tearsheets_processed': 8},
    )

    with patch(
        'lightweight_reference_refresh.lightweight_refresh_references_from_content',
        return_value={
            'success': True,
            'jobs_updated': 3,
            'time_seconds': 0.01,
            'xml_content': '<?xml version="1.0"?><source></source>',
        },
    ) as refresh_mock, patch(
        'lightweight_reference_refresh.save_references_to_database',
        return_value=True,
    ) as save_mock:
        result = refresh_all_feed_references(generator)

    assert result['success'] is True
    assert result['jobs_updated'] == 3
    assert result['database_saved'] is True
    assert result['feeds_covered'] == ['v2', 'stsi_indeed', 'stsi_ziprecruiter']

    kwargs = generator.generate_fresh_xml.call_args.kwargs
    tearsheet_ids = kwargs['tearsheet_ids']
    assert tearsheet_ids == all_xml_feed_tearsheet_ids()
    assert TEARSHEET_STSI_INDEED in tearsheet_ids
    assert TEARSHEET_STSI_ZIPRECRUITER in tearsheet_ids
    for tid in V2_TEARSHEET_IDS:
        assert tid in tearsheet_ids
    assert kwargs['allow_empty'] is True

    refresh_mock.assert_called_once()
    save_mock.assert_called_once()


def test_refresh_all_feed_references_fails_when_db_save_fails():
    from lightweight_reference_refresh import refresh_all_feed_references

    generator = MagicMock()
    generator.generate_fresh_xml.return_value = (
        '<?xml version="1.0"?><source></source>',
        {'job_count': 1, 'xml_size_bytes': 20, 'tearsheets_processed': 8},
    )

    with patch(
        'lightweight_reference_refresh.lightweight_refresh_references_from_content',
        return_value={
            'success': True,
            'jobs_updated': 1,
            'time_seconds': 0.01,
            'xml_content': '<?xml version="1.0"?><source></source>',
        },
    ), patch(
        'lightweight_reference_refresh.save_references_to_database',
        return_value=False,
    ):
        result = refresh_all_feed_references(generator)

    assert result['success'] is False
    assert result['database_saved'] is False
    assert 'database' in result['error'].lower()
