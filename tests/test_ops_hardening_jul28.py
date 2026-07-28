"""Regression tests for undated-tenure years-gate wording and Indeed dual-path park."""
from screening.post_processing import _shortfall_gap_label, enforce_years_hard_gate
from feeds.feed_config import channel_feeds_for_upload, indeed_native_publish_enabled


def test_undated_zero_years_uses_unverified_tenure_not_critical():
    label = _shortfall_gap_label(
        'Power BI',
        5.0,
        0.0,
        {'calculation': 'No dated role history — tenure unverified'},
    )
    assert label.startswith('UNVERIFIED TENURE:')
    assert 'CRITICAL:' not in label


def test_transferable_undated_uses_transferable_label():
    label = _shortfall_gap_label(
        'Power BI',
        5.0,
        0.0,
        {
            'calculation': '0yr Power BI but 6yr Tableau equivalent',
            'gap_type': 'TRANSFERABLE',
        },
    )
    assert 'TRANSFERABLE' in label
    assert 'CRITICAL:' not in label


def test_dated_shortfall_still_critical():
    label = _shortfall_gap_label(
        'Python',
        5.0,
        1.0,
        {'calculation': 'Jan 2024 – Dec 2024 = 12 months'},
    )
    assert label.startswith('CRITICAL:')


def test_years_gate_writes_unverified_tenure_into_gaps():
    result = {
        'match_score': 82,
        'gaps_identified': '',
        'years_analysis': {
            'Power BI': {
                'required_years': 5,
                'estimated_years': 0.0,
                'meets_requirement': False,
                'calculation': 'No dated role history — tenure unverified',
            }
        },
    }
    enforce_years_hard_gate(
        result, job_id=35471, job_title='Senior Power BI Developer',
        resume_text='Power BI Lead', recheck_fn=lambda *a, **k: None,
    )
    assert result['match_score'] <= 60
    assert 'UNVERIFIED TENURE' in (result.get('gaps_identified') or '')
    assert 'CRITICAL: Power BI' not in (result.get('gaps_identified') or '')


def test_channel_feeds_park_indeed_when_plan_b_enabled(monkeypatch):
    monkeypatch.setenv('INDEED_TEARSHEET_PUBLISH_ENABLED', 'true')
    assert indeed_native_publish_enabled() is True
    feeds = channel_feeds_for_upload()
    indeed = next(f for f in feeds if f['key'] == 'stsi_indeed')
    assert indeed.get('force_empty') is True
    zipf = next(f for f in feeds if f['key'] == 'stsi_ziprecruiter')
    assert not zipf.get('force_empty')


def test_channel_feeds_normal_when_plan_b_off(monkeypatch):
    monkeypatch.setenv('INDEED_TEARSHEET_PUBLISH_ENABLED', 'false')
    feeds = channel_feeds_for_upload()
    indeed = next(f for f in feeds if f['key'] == 'stsi_indeed')
    assert not indeed.get('force_empty')
