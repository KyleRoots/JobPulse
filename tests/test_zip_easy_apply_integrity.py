"""Zip Easy Apply / board-notification contact integrity tests."""

import logging

import pytest

from email_inbound_service._core import _InboundCore
from email_inbound_service.extraction_mixin import ExtractionMixin
from email_inbound_service.resume_mixin import ResumeMixin
from utils.candidate_name_extraction import (
    coalesce_candidate_email,
    is_job_board_relay_email,
    is_owned_intake_mailbox,
)


class _Harness(ResumeMixin, ExtractionMixin, _InboundCore):
    def __init__(self):
        self.logger = logging.getLogger('test_zip_easy_apply')
        self.openai_client = None


@pytest.fixture
def parser():
    return _Harness()


class TestBoardRelayEmail:
    def test_noreply_zip_is_relay(self):
        assert is_job_board_relay_email('noreply@ziprecruiter.com') is True

    def test_indeedemail_is_relay(self):
        assert is_job_board_relay_email('abc123@indeedemail.com') is True

    def test_real_gmail_is_not_relay(self):
        assert is_job_board_relay_email('tim.zarembski@gmail.com') is False

    def test_coalesce_skips_relay_for_resume_email(self):
        assert coalesce_candidate_email(
            'noreply@ziprecruiter.com',
            'tim.zarembski@gmail.com',
        ) == 'tim.zarembski@gmail.com'

    def test_coalesce_all_relay_returns_none(self):
        assert coalesce_candidate_email('noreply@ziprecruiter.com') is None

    def test_apply_mailbox_is_relay(self):
        assert is_owned_intake_mailbox('apply@myticas.com') is True
        assert is_job_board_relay_email('apply@myticas.com') is True
        assert is_job_board_relay_email('info@myticas.com') is True
        # STSI privacy contact must not be scraped as a candidate email
        assert is_owned_intake_mailbox('stsioffice@stsigroup.com') is True
        assert is_job_board_relay_email('stsioffice@stsigroup.com') is True

    def test_coalesce_skips_apply_greeting_for_resume_email(self):
        # Zip body greets "Hi apply@myticas.com" — must not beat résumé contact.
        assert coalesce_candidate_email(
            'apply@myticas.com',
            'bolnikstacey@gmail.com',
        ) == 'bolnikstacey@gmail.com'

    def test_recruiter_myticas_email_is_not_intake_mailbox(self):
        assert is_owned_intake_mailbox('kmiller@stsigroup.com') is False
        assert is_job_board_relay_email('kroots@myticas.com') is False


class TestZipSubjectAndBodyExtraction:
    def test_great_match_subject_name(self, parser):
        subject = "⭐ Great Match: Tim Zarembski for 'Document Control Specialist (35429)'"
        body = 'noreply@ziprecruiter.com We found a new candidate for you.'
        cand = parser.extract_candidate_from_email(
            subject, body, 'ZipRecruiter Job Board'
        )
        assert cand.get('first_name') == 'Tim'
        assert cand.get('last_name') == 'Zarembski'
        assert cand.get('email') is None  # noreply skipped

    def test_zip_greeting_apply_mailbox_not_used_as_email(self, parser):
        subject = "⭐ Great Match: Stacey Bolnik for 'Project Coordinator (35511)'"
        body = 'Hi apply@myticas.com, We found a new candidate for you.'
        cand = parser.extract_candidate_from_email(
            subject, body, 'ZipRecruiter Job Board'
        )
        assert cand.get('first_name') == 'Stacey'
        assert cand.get('last_name') == 'Bolnik'
        assert cand.get('email') is None  # apply@ skipped; résumé supplies contact later

    def test_new_candidate_subject_name(self, parser):
        subject = "New candidate: Melissa Schrader for 'Document Control Specialist (35429)'"
        cand = parser.extract_candidate_from_email(
            subject, '', 'ZipRecruiter Job Board'
        )
        assert cand.get('first_name') == 'Melissa'
        assert cand.get('last_name') == 'Schrader'

    def test_source_and_job_id(self, parser):
        sender = 'ZipRecruiter <noreply@ziprecruiter.com>'
        subject = "⭐ Great Match: Tim Zarembski for 'Document Control Specialist (35429)'"
        assert parser.detect_source(sender, subject, 'ZipRecruiter') == 'ZipRecruiter Job Board'
        assert parser.extract_bullhorn_job_id(subject, '') == 35429

    def test_map_to_bullhorn_prefers_resume_over_relay(self, parser):
        email_data = {
            'first_name': 'Tim',
            'last_name': 'Zarembski',
            'email': 'noreply@ziprecruiter.com',
        }
        resume_data = {'email': 'tim.zarembski@gmail.com', 'phone': '555-0100'}
        mapped = parser.map_to_bullhorn_fields(
            email_data, resume_data, 'ZipRecruiter Job Board'
        )
        assert mapped['email'] == 'tim.zarembski@gmail.com'
        assert mapped['firstName'] == 'Tim'
        assert mapped['source'] == 'ZipRecruiter Job Board'

    def test_map_to_bullhorn_prefers_resume_over_apply_mailbox(self, parser):
        email_data = {
            'first_name': 'Stacey',
            'last_name': 'Bolnik',
            'email': 'apply@myticas.com',
        }
        resume_data = {'email': 'bolnikstacey@gmail.com', 'phone': '407.435.1694'}
        mapped = parser.map_to_bullhorn_fields(
            email_data, resume_data, 'ZipRecruiter Job Board'
        )
        assert mapped['email'] == 'bolnikstacey@gmail.com'
