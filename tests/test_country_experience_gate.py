"""Tests for country-scoped years-of-experience hard gate."""

from screening.country_experience import (
    COUNTRY_CANADA,
    COUNTRY_US,
    country_experience_allows_qualify,
    enforce_country_experience_gate,
    parse_country_experience_requirements,
)
from screening.post_processing import years_tenure_allows_qualify


class TestParseCountryExperienceRequirements:
    def test_canada_experience_in(self):
        reqs = parse_country_experience_requirements(
            "Must have 5+ years of experience in Canada"
        )
        assert reqs == [(COUNTRY_CANADA, 5.0)]

    def test_canadian_work_experience(self):
        reqs = parse_country_experience_requirements(
            "Minimum 5 years Canadian work experience required."
        )
        assert reqs == [(COUNTRY_CANADA, 5.0)]

    def test_reside_in_canada(self):
        reqs = parse_country_experience_requirements(
            "Candidate must reside in Canada for 5 years."
        )
        assert reqs == [(COUNTRY_CANADA, 5.0)]

    def test_us_experience(self):
        reqs = parse_country_experience_requirements(
            "3+ years of experience in the United States"
        )
        assert reqs == [(COUNTRY_US, 3.0)]

    def test_no_country_scope(self):
        assert parse_country_experience_requirements(
            "Must have 5+ years of Python experience"
        ) == []


class TestEnforceCountryExperienceGate:
    def test_us_candidate_blocks_on_canada_requirement(self):
        result = {
            'match_score': 88,
            'years_analysis': {},
            'gaps_identified': '',
            'key_requirements': '5+ years of experience in Canada',
        }
        enforce_country_experience_gate(
            result,
            job_id=1,
            custom_requirements='5+ years of experience in Canada',
            job_description='',
            candidate_country='United States',
        )
        assert result['_country_experience_blocks_qualify'] is True
        assert result['_years_tenure_blocks_qualify'] is True
        assert result['match_score'] == 60
        assert 'Canada' in result['gaps_identified']
        assert years_tenure_allows_qualify(result) is False
        assert country_experience_allows_qualify(result) is False

    def test_sufficient_canadian_years_passes(self):
        result = {
            'match_score': 85,
            'years_analysis': {
                'Canadian professional experience': {
                    'required_years': 5,
                    'estimated_years': 7.2,
                    'meets_requirement': True,
                }
            },
            'gaps_identified': '',
            'key_requirements': '',
        }
        enforce_country_experience_gate(
            result,
            job_id=2,
            custom_requirements='Minimum 5 years Canadian work experience',
            job_description='',
            candidate_country='Canada',
        )
        assert result['_country_experience_blocks_qualify'] is False
        assert result['match_score'] == 85
        assert years_tenure_allows_qualify(result) is True

    def test_short_canadian_years_blocks(self):
        result = {
            'match_score': 90,
            'years_analysis': {
                'Canadian professional experience': {
                    'required_years': 5,
                    'estimated_years': 1.5,
                    'meets_requirement': False,
                }
            },
            'gaps_identified': '',
            'key_requirements': '',
        }
        enforce_country_experience_gate(
            result,
            job_id=3,
            custom_requirements='5 years experience in Canada',
            job_description='',
            candidate_country='Canada',
        )
        assert result['_country_experience_blocks_qualify'] is True
        assert result['match_score'] == 60

    def test_no_requirement_noop(self):
        result = {
            'match_score': 82,
            'years_analysis': {},
            'gaps_identified': '',
            'key_requirements': '5+ years Python',
        }
        enforce_country_experience_gate(
            result, 4, '', 'Need a strong Python developer.', candidate_country='US'
        )
        assert result.get('_country_experience_blocks_qualify') is False
        assert result['match_score'] == 82

    def test_missing_analysis_fail_closed(self):
        """Requirement present, no years_analysis key → treat as 0 and block."""
        result = {
            'match_score': 91,
            'years_analysis': {
                'Python': {'required_years': 5, 'estimated_years': 8, 'meets_requirement': True}
            },
            'gaps_identified': '',
            'key_requirements': '',
        }
        enforce_country_experience_gate(
            result,
            job_id=5,
            custom_requirements='Must have at least 5 years in Canada',
            job_description='',
            candidate_country='Canada',
        )
        assert result['_country_experience_blocks_qualify'] is True
        assert result['match_score'] == 60
