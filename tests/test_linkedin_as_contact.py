"""LinkedIn /in/ URL as a recruiter-reachable contact channel on intake."""

import logging

import pytest

from email_inbound_service._core import _InboundCore
from email_inbound_service.extraction_mixin import ExtractionMixin
from email_inbound_service.resume_mixin import ResumeMixin
from utils.candidate_name_extraction import (
    has_candidate_contact,
    resolve_linkedin_profile_url,
)


class _Harness(ResumeMixin, ExtractionMixin, _InboundCore):
    def __init__(self):
        self.logger = logging.getLogger('test_linkedin_as_contact')
        self.openai_client = None


@pytest.fixture
def parser():
    return _Harness()


class TestLinkedInContactHelpers:
    def test_resolve_profile_url_from_resume_text(self):
        text = "Reach me at https://www.linkedin.com/in/Jane-Doe-123 or email."
        assert resolve_linkedin_profile_url(text) == \
            "https://www.linkedin.com/in/jane-doe-123"

    def test_resolve_ignores_company_pages(self):
        assert resolve_linkedin_profile_url("linkedin.com/company/acme") is None

    def test_has_contact_linkedin_alone(self):
        assert has_candidate_contact(
            None, None, "https://www.linkedin.com/in/jane-doe"
        ) is True

    def test_has_contact_rejects_empty(self):
        assert has_candidate_contact(None, None, None) is False
        assert has_candidate_contact("apply@myticas.com", None, None) is False


class TestMapLinkedInToBullhorn:
    def test_map_sets_custom_text9_from_resume_text(self, parser):
        email_data = {'first_name': 'Jane', 'last_name': 'Doe'}
        resume_data = {
            'raw_text': (
                'Jane Doe\nlinkedin.com/in/jane-doe-99\n'
                'Experienced project coordinator.'
            ),
        }
        mapped = parser.map_to_bullhorn_fields(
            email_data, resume_data, 'ZipRecruiter Job Board'
        )
        assert mapped['customText9'] == 'https://www.linkedin.com/in/jane-doe-99'
        assert mapped.get('email') is None
        assert mapped['firstName'] == 'Jane'

    def test_map_prefers_explicit_linkedin_field(self, parser):
        email_data = {'first_name': 'Jane', 'last_name': 'Doe'}
        resume_data = {
            'linkedin_url': 'https://linkedin.com/in/ExplicitSlug',
            'raw_text': 'also mentions linkedin.com/in/other-person',
        }
        mapped = parser.map_to_bullhorn_fields(
            email_data, resume_data, 'LinkedIn Job Board'
        )
        assert mapped['customText9'] == 'https://www.linkedin.com/in/explicitslug'
