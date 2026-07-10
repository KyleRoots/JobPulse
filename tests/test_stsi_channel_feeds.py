"""Tests for STSI channel XML feeds (Indeed + ZipRecruiter)."""
import pytest
from unittest.mock import MagicMock, patch

from feeds.feed_config import (
    CHANNEL_FEEDS,
    V2_TEARSHEET_IDS,
    TEARSHEET_STSI_INDEED,
    TEARSHEET_STSI_LINKEDIN,
    TEARSHEET_STSI_ZIPRECRUITER,
    SOURCE_INDEED,
    SOURCE_ZIPRECRUITER,
    SOURCE_LINKEDIN,
    STSI_INDEED_FILENAME_DEV,
    STSI_ZIPRECRUITER_FILENAME_DEV,
)
from xml_integration_service import XMLIntegrationService


class TestFeedConfig:
    def test_v2_includes_linkedin_stsi_only(self):
        assert TEARSHEET_STSI_LINKEDIN in V2_TEARSHEET_IDS
        assert TEARSHEET_STSI_INDEED not in V2_TEARSHEET_IDS
        assert TEARSHEET_STSI_ZIPRECRUITER not in V2_TEARSHEET_IDS

    def test_channel_feeds_cover_indeed_and_zip(self):
        keys = {f['key'] for f in CHANNEL_FEEDS}
        assert keys == {'stsi_indeed', 'stsi_ziprecruiter'}
        assert CHANNEL_FEEDS[0]['source_channel'] == SOURCE_INDEED
        assert CHANNEL_FEEDS[1]['source_channel'] == SOURCE_ZIPRECRUITER
        assert CHANNEL_FEEDS[0]['filename_dev'] == STSI_INDEED_FILENAME_DEV
        assert CHANNEL_FEEDS[1]['filename_dev'] == STSI_ZIPRECRUITER_FILENAME_DEV


class TestApplicationUrlSourceChannel:
    def setup_method(self):
        self.svc = XMLIntegrationService()

    def test_stsi_indeed_url(self):
        url = self.svc._generate_job_application_url(
            '12345', 'Software Engineer', 'STSI (Staffing Technical Services Inc.)',
            source_channel=SOURCE_INDEED,
        )
        assert url.startswith('https://apply.stsigroup.com/12345/')
        assert '/?source=Indeed' in url

    def test_stsi_ziprecruiter_url(self):
        url = self.svc._generate_job_application_url(
            '99', 'Analyst', 'STSI (Staffing Technical Services Inc.)',
            source_channel=SOURCE_ZIPRECRUITER,
        )
        assert '/?source=ZipRecruiter' in url

    def test_myticas_defaults_linkedin(self):
        url = self.svc._generate_job_application_url(
            '1', 'Developer', 'Myticas Consulting',
        )
        assert url.startswith('https://apply.myticas.com/')
        assert '/?source=LinkedIn' in url


class TestEmptyFeedGeneration:
    def test_build_clean_xml_empty_source(self):
        from simplified_xml_generator import SimplifiedXMLGenerator
        gen = SimplifiedXMLGenerator(db=None)
        xml, refs = gen._build_clean_xml([], {}, source_channel=SOURCE_INDEED)
        assert '<?xml' in xml
        assert '<source>' in xml
        assert '<job>' not in xml
        assert refs == {}

    def test_generate_fresh_xml_allow_empty(self):
        from simplified_xml_generator import SimplifiedXMLGenerator
        gen = SimplifiedXMLGenerator(db=MagicMock())
        mock_bh = MagicMock()
        mock_bh.authenticate.return_value = True
        mock_bh.get_tearsheet_jobs.return_value = []

        with patch.object(gen, '_get_bullhorn_service', return_value=mock_bh), \
             patch.object(gen, '_load_references_from_database', return_value={}):
            xml, stats = gen.generate_fresh_xml(
                tearsheet_ids=[TEARSHEET_STSI_INDEED],
                source_channel=SOURCE_INDEED,
                allow_empty=True,
            )
        assert stats['job_count'] == 0
        assert '<source>' in xml
        assert '<job>' not in xml

    def test_generate_fresh_xml_raises_without_allow_empty(self):
        from simplified_xml_generator import SimplifiedXMLGenerator
        gen = SimplifiedXMLGenerator(db=MagicMock())
        mock_bh = MagicMock()
        mock_bh.authenticate.return_value = True
        mock_bh.get_tearsheet_jobs.return_value = []

        with patch.object(gen, '_get_bullhorn_service', return_value=mock_bh), \
             patch.object(gen, '_load_references_from_database', return_value={}):
            with pytest.raises(Exception, match='No jobs found'):
                gen.generate_fresh_xml(tearsheet_ids=[TEARSHEET_STSI_INDEED])


class TestSourceAttributionChannels:
    def test_indeed_explicit_source(self):
        from source_attribution import resolve_source
        assert resolve_source('Indeed', '', '') == 'Indeed Job Board'

    def test_ziprecruiter_explicit_source(self):
        from source_attribution import resolve_source
        assert resolve_source('ZipRecruiter', '', '') == 'ZipRecruiter Job Board'

    def test_referrer_beats_explicit_for_direct_board(self):
        from source_attribution import resolve_source
        assert resolve_source('LinkedIn', 'https://www.indeed.com/viewjob', '') == 'Indeed Job Board'
