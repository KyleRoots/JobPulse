"""Zip Easy Apply / board-notification contact integrity tests."""

import logging

import pytest

from email_inbound_service._core import _InboundCore
from email_inbound_service.extraction_mixin import ExtractionMixin
from email_inbound_service.resume_mixin import ResumeMixin
from utils.candidate_name_extraction import (
    coalesce_candidate_email,
    is_job_board_relay_email,
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
