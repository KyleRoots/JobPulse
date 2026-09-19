"""Industrial jobs must not score a silent shift preference as a miss."""

from screening.post_processing import relax_industrial_schedule_silence
from screening.system_prompt import build_system_message


NPI_GAP = (
    "Experience leading new product introduction into a manufacturing facility "
    "is required; resume shows process improvement but no plant NPI leadership."
)
SHIFT_GAP = (
    "Availability for second shift (4:00 PM start) is required; "
    "no evidence of shift availability found in resume."
)


def test_prompt_tells_industrial_jobs_not_to_penalize_shift_silence():
    msg = build_system_message("Must have valid work authorization.")
    assert "13c. INDUSTRIAL SCHEDULE SILENCE" in msg
    assert "silence is NOT a gap" in msg
    assert "IT/software/data/cloud/cybersecurity" in msg


def test_manufacturing_shift_silence_is_removed_and_score_stays_under_threshold():
    result = {
        'match_score': 58,
        'technical_score': 58,
        'gaps_identified': f"{NPI_GAP} | {SHIFT_GAP}",
    }
    relax_industrial_schedule_silence(
        result,
        35795,
        "Manufacturing Engineer - 2nd Shift",
        "Lead the introduction of new products into the facility. SECOND shift.",
    )
    assert result['match_score'] == 66
    assert result['technical_score'] == 66
    assert "shift" not in result['gaps_identified'].lower()
    assert "new product introduction" in result['gaps_identified']


def test_shift_silence_alone_can_move_the_score():
    result = {
        'match_score': 72,
        'technical_score': 72,
        'gaps_identified': SHIFT_GAP,
    }
    relax_industrial_schedule_silence(
        result, 1, "Warehouse Associate", "Forklift, second shift.",
    )
    assert result['match_score'] == 80
    assert result['gaps_identified'] == ''


def test_explicit_shift_conflict_is_kept():
    result = {
        'match_score': 70,
        'technical_score': 70,
        'gaps_identified': "Candidate states first shift only; job requires second shift.",
    }
    relax_industrial_schedule_silence(
        result, 1, "Manufacturing Engineer", "Second shift, 4pm start.",
    )
    assert result['match_score'] == 70
    assert "first shift only" in result['gaps_identified']


def test_it_job_is_unchanged():
    result = {
        'match_score': 58,
        'technical_score': 58,
        'gaps_identified': (
            "On-call nights required; no evidence of night availability found in resume."
        ),
    }
    relax_industrial_schedule_silence(
        result, 1, "Software Developer", "Build the SaaS API. Occasional nights.",
    )
    assert result['match_score'] == 58
    assert "no evidence" in result['gaps_identified']
