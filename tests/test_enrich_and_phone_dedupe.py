"""Regression tests for inbound enrich + phone-dedupe name guard.

Covers the Happy Friday / Kyle Roots Zip test case:
1. Blank primary email is enrichable (was previously skipped).
2. Phone-only duplicate hits with conflicting names require AI confirmation
   and are skipped when confidence is low — so a dummy shell with a reused
   mobile does not swallow a new applicant.
"""

import logging
from unittest.mock import MagicMock

import pytest

from email_inbound_service._core import _InboundCore
from email_inbound_service.ai_mixin import AIMixin
from email_inbound_service.extraction_mixin import ExtractionMixin
from email_inbound_service.resume_mixin import ResumeMixin


class _Harness(ResumeMixin, AIMixin, ExtractionMixin, _InboundCore):
    def __init__(self):
        self.logger = logging.getLogger('test_enrich_dedupe')
        self.openai_client = None  # force deterministic non-AI fallback


@pytest.fixture
def mapper():
    return _Harness()


class TestEnrichBlankPrimaryEmail:
    def test_fills_blank_primary_email(self, mapper):
        existing = {
            'email': '',
            'phone': '+1 844 698-4227',
            'mobile': '+1 613 799-2691',
            'source': 'LinkedIn Job Board',
        }
        new_data = {
            'email': 'kyleroots00@gmail.com',
            'phone': '(613) 799-2691',
            'occupation': 'Digital Marketing Manager',
        }
        enriched = mapper._build_enrichment_update(existing, new_data)
        assert enriched.get('email') == 'kyleroots00@gmail.com'
        assert enriched.get('occupation') == 'Digital Marketing Manager'
        # Populated phone/mobile must not be overwritten
        assert 'phone' not in enriched
        assert 'mobile' not in enriched

    def test_does_not_overwrite_existing_primary_email(self, mapper):
        existing = {'email': 'old@example.com'}
        new_data = {'email': 'new@example.com', 'occupation': 'Engineer'}
        enriched = mapper._build_enrichment_update(existing, new_data)
        assert 'email' not in enriched
        assert enriched.get('occupation') == 'Engineer'


class TestPhoneMatchNameConflict:
    def test_conflict_helper_detects_mismatch(self, mapper):
        assert mapper._phone_match_names_conflict(
            'Kyle', 'Roots', {'firstName': 'Happy', 'lastName': 'Friday'}
        ) is True

    def test_conflict_helper_same_name_no_conflict(self, mapper):
        assert mapper._phone_match_names_conflict(
            'Kyle', 'Roots', {'firstName': 'Kyle', 'lastName': 'Roots'}
        ) is False

    def test_conflict_helper_incomplete_name_no_conflict(self, mapper):
        assert mapper._phone_match_names_conflict(
            'Kyle', 'Roots', {'firstName': '', 'lastName': 'Friday'}
        ) is False

    def test_phone_match_skipped_when_names_conflict_without_ai(self, mapper):
        """Happy Friday shell with Kyle's mobile must not win on phone alone."""
        bh = MagicMock()
        bh.search_candidates.side_effect = [
            [],  # email search
            [{
                'id': 3875246,
                'firstName': 'Happy',
                'lastName': 'Friday',
                'email': '',
                'phone': '+1 844 698-4227',
                'mobile': '+1 613 799-2691',
                'status': 'Active',
            }],
        ]
        mapper._resolve_archive_redirect = MagicMock(
            side_effect=lambda match, _bh: match.get('id')
        )

        candidate_id, confidence = mapper.find_duplicate_candidate(
            'kyleroots00@gmail.com',
            '(613) 799-2691',
            'Kyle',
            'Roots',
            bh,
        )
        assert candidate_id is None
        assert confidence == 0.0

    def test_phone_match_accepted_when_names_align(self, mapper):
        bh = MagicMock()
        bh.search_candidates.side_effect = [
            [],
            [{
                'id': 1001,
                'firstName': 'Kyle',
                'lastName': 'Roots',
                'email': '',
                'phone': '6137992691',
                'status': 'Active',
            }],
        ]
        mapper._resolve_archive_redirect = MagicMock(
            side_effect=lambda match, _bh: match.get('id')
        )

        candidate_id, confidence = mapper.find_duplicate_candidate(
            'kyleroots00@gmail.com',
            '6137992691',
            'Kyle',
            'Roots',
            bh,
        )
        assert candidate_id == 1001
        assert confidence == 0.9

    def test_phone_match_accepted_when_ai_confirms_despite_name_diff(self, mapper):
        """Nickname / spelling variants still merge when AI says same person."""
        bh = MagicMock()
        bh.search_candidates.side_effect = [
            [],
            [{
                'id': 2002,
                'firstName': 'Bob',
                'lastName': 'Smith',
                'email': '',
                'phone': '5551234567',
                'status': 'Active',
            }],
        ]
        mapper._resolve_archive_redirect = MagicMock(
            side_effect=lambda match, _bh: match.get('id')
        )
        mapper._ai_validate_duplicate = MagicMock(return_value=0.85)

        candidate_id, confidence = mapper.find_duplicate_candidate(
            'bob@example.com',
            '5551234567',
            'Robert',
            'Smith',
            bh,
        )
        assert candidate_id == 2002
        assert confidence == 0.9  # max(0.85, 0.9)
        mapper._ai_validate_duplicate.assert_called_once()
