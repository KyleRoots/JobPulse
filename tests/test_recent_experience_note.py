"""Employment-gap sentence on Scout notes (qualified and not recommended)."""
from unittest.mock import Mock

from screening.note_builder import (
    NoteBuilderMixin,
    format_recent_experience_line,
    split_employment_gap_clauses,
)
from screening.post_processing import enforce_employment_continuity_gap


def test_formats_iso_end_date_and_duration():
    line = format_recent_experience_line(
        "Employment gap: candidate last employed 2022-12 (44 months ago)."
    )
    assert line == (
        "Recent experience: last employed Dec 2022, "
        "44 months (3 years 8 months) with no recent work."
    )


def test_leaves_midcareer_gap_out_of_recent_experience():
    gaps = (
        "Missing Kubernetes. | Mid-career employment gap: 14 months between "
        "Role A end and Role B start."
    )
    recent = format_recent_experience_line(gaps)
    gap_blob, other = split_employment_gap_clauses(gaps)
    assert recent == ''
    assert 'Kubernetes' in other
    assert 'Mid-career' in other
    assert gap_blob == ''


def test_qualified_note_block_shows_recent_experience_without_full_gaps():
    match = Mock(
        bullhorn_job_id=35720,
        job_title='Customer Service Support Intermediate',
        match_score=86.0,
        technical_score=86.0,
        gaps_identified=(
            "Employment gap: candidate last employed 2022-12 (44 months ago)."
        ),
        match_summary='Strong call-center fit in Phoenix.',
        skills_match='Call center, CRM, Microsoft Office',
        prestige_boost_applied=False,
        prestige_employer=None,
    )
    lines = NoteBuilderMixin()._format_match_note_block(match, {}, show_gaps=False)
    joined = '\n'.join(lines)
    assert 'Recent experience: last employed Dec 2022, 44 months' in joined
    assert 'with no recent work.' in joined
    assert 'Gaps:' not in joined
    assert 'Summary: Strong call-center fit in Phoenix.' in joined


def test_not_recommended_does_not_repeat_employment_gap_in_gaps_line():
    match = Mock(
        bullhorn_job_id=35720,
        job_title='Customer Service Support Intermediate',
        match_score=72.0,
        technical_score=72.0,
        gaps_identified=(
            "Missing Salesforce. | Employment gap: candidate last employed "
            "Dec 2022 (44 months ago)."
        ),
        match_summary='Relevant CS background.',
        skills_match='Call center',
        prestige_boost_applied=False,
        prestige_employer=None,
    )
    lines = NoteBuilderMixin()._format_match_note_block(match, {}, show_gaps=True)
    joined = '\n'.join(lines)
    assert 'Recent experience: last employed Dec 2022' in joined
    assert 'Gaps: Missing Salesforce.' in joined
    assert joined.count('candidate last employed') == 0


def test_no_line_when_currently_employed():
    match = Mock(
        bullhorn_job_id=1,
        job_title='Analyst',
        match_score=88.0,
        technical_score=88.0,
        gaps_identified='Missing Databricks production experience.',
        match_summary='Solid data engineer.',
        skills_match='Spark',
        prestige_boost_applied=False,
        prestige_employer=None,
    )
    lines = NoteBuilderMixin()._format_match_note_block(match, {}, show_gaps=False)
    joined = '\n'.join(lines)
    assert 'Recent experience:' not in joined


def test_enforcer_stamps_gaps_text_even_when_penalty_already_correct():
    result = {
        'match_score': 86,
        'technical_score': 86,
        'gaps_identified': 'Strong CS background otherwise.',
        'employment_gap_analysis': {
            'gap_months': 44,
            'penalty_applied': 15,
            'last_role_end_date': '2022-12',
        },
    }
    enforce_employment_continuity_gap(result, job_id=35720)
    assert result['match_score'] == 86
    assert 'candidate last employed 2022-12 (44 months ago)' in result['gaps_identified']


def test_enforcer_does_not_duplicate_existing_gap_sentence():
    result = {
        'match_score': 86,
        'technical_score': 86,
        'gaps_identified': (
            "Employment gap: candidate last employed Dec 2022 (44 months ago)."
        ),
        'employment_gap_analysis': {
            'gap_months': 44,
            'penalty_applied': 15,
            'last_role_end_date': '2022-12',
        },
    }
    enforce_employment_continuity_gap(result, job_id=35720)
    assert result['gaps_identified'].count('candidate last employed') == 1
