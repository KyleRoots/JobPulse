"""Tests for Phase 1 ops early-warning (fingerprint dedupe + signal thresholds)."""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from services.ops_early_warning import (
    SEVERITY_CRITICAL,
    SEVERITY_NONE,
    SEVERITY_WARNING,
    HealthSignal,
    build_fingerprint,
    classify_inbound_null_rate,
    classify_missed_runs,
    classify_age_minutes,
    classify_count,
    classify_screening_stall_age,
    max_severity,
    run_ops_early_warning_check,
    should_send_alert,
)

NOW = datetime(2026, 8, 10, 13, 0, 0)


def _sig(key, severity='warning', title=None):
    return HealthSignal(
        key=key,
        severity=severity,
        title=title or key,
        detail='test',
    )


class TestClassifyInbound:
    def test_below_min_sample_is_none(self):
        assert classify_inbound_null_rate(
            1.0, 3, min_completed=5, warn_rate=0.4, critical_rate=0.7
        ) == SEVERITY_NONE

    def test_healthy_null_rate_is_none(self):
        # ~10% null ≈ healthy baseline
        assert classify_inbound_null_rate(
            0.10, 20, min_completed=5, warn_rate=0.4, critical_rate=0.7
        ) == SEVERITY_NONE

    def test_warn_band(self):
        assert classify_inbound_null_rate(
            0.45, 20, min_completed=5, warn_rate=0.4, critical_rate=0.7
        ) == SEVERITY_WARNING

    def test_critical_cliff(self):
        assert classify_inbound_null_rate(
            0.95, 12, min_completed=5, warn_rate=0.4, critical_rate=0.7
        ) == SEVERITY_CRITICAL

    def test_boundary_warn_inclusive(self):
        assert classify_inbound_null_rate(
            0.40, 10, min_completed=5, warn_rate=0.4, critical_rate=0.7
        ) == SEVERITY_WARNING


class TestClassifyMissedAndAge:
    def test_missed_runs_warn(self):
        # 5min job, age 20min → 4× → warn if miss_warn=3
        assert classify_missed_runs(20, 5, 3, 6) == SEVERITY_WARNING

    def test_missed_runs_critical(self):
        assert classify_missed_runs(40, 5, 3, 6) == SEVERITY_CRITICAL

    def test_missed_runs_ok(self):
        assert classify_missed_runs(8, 5, 3, 6) == SEVERITY_NONE

    def test_missed_runs_absurd_stamp_ignored(self):
        # ~34-day frozen stamp must not CRITICAL a 5-minute job
        assert classify_missed_runs(
            49600, 5, 3, 6, absurd_max_min=24 * 60
        ) == SEVERITY_NONE

    def test_age_bands(self):
        assert classify_age_minutes(45, 30, 60) == SEVERITY_WARNING
        assert classify_age_minutes(90, 30, 60) == SEVERITY_CRITICAL
        assert classify_age_minutes(10, 30, 60) == SEVERITY_NONE

    def test_count_bands(self):
        assert classify_count(30, 25, 50) == SEVERITY_WARNING
        assert classify_count(50, 25, 50) == SEVERITY_CRITICAL

    def test_zombie_inflight_suppressed_when_completions_flow(self):
        assert classify_screening_stall_age(
            146880,
            inflight=1,
            completed_recent=3,
            warn_min=30,
            critical_min=60,
            zombie_age_min=24 * 60,
        ) == SEVERITY_NONE

    def test_live_stall_still_critical_without_completions(self):
        assert classify_screening_stall_age(
            90,
            inflight=2,
            completed_recent=0,
            warn_min=30,
            critical_min=60,
            zombie_age_min=24 * 60,
        ) == SEVERITY_CRITICAL


class TestFingerprintAndSend:
    def test_fingerprint_stable_and_sorted(self):
        a = build_fingerprint([_sig('b', 'warning'), _sig('a', 'critical')])
        b = build_fingerprint([_sig('a', 'critical'), _sig('b', 'warning')])
        assert a == b
        assert 'a:critical' in a
        assert 'b:warning' in a

    def test_fingerprint_changes_when_signal_set_changes(self):
        a = build_fingerprint([_sig('inbound_null_bh', 'critical')])
        b = build_fingerprint([_sig('screening_stall', 'critical')])
        assert a != b

    def test_fingerprint_none_when_quiet(self):
        assert build_fingerprint([_sig('x', SEVERITY_NONE)]) == 'none'

    def test_max_severity(self):
        assert max_severity([SEVERITY_NONE, SEVERITY_WARNING]) == SEVERITY_WARNING
        assert max_severity([SEVERITY_WARNING, SEVERITY_CRITICAL]) == SEVERITY_CRITICAL

    def test_suppress_same_fingerprint_inside_cooldown(self):
        fp = build_fingerprint([_sig('inbound_null_bh', 'critical')])
        send, reason = should_send_alert(
            SEVERITY_CRITICAL,
            fp,
            SEVERITY_CRITICAL,
            fp,
            NOW - timedelta(hours=1),
            NOW,
            6.0,
        )
        assert send is False
        assert 'cooldown' in reason

    def test_send_when_fingerprint_changes(self):
        old_fp = build_fingerprint([_sig('inbound_null_bh', 'warning')])
        new_fp = build_fingerprint([_sig('screening_stall', 'warning')])
        send, reason = should_send_alert(
            SEVERITY_WARNING,
            new_fp,
            SEVERITY_WARNING,
            old_fp,
            NOW - timedelta(hours=1),
            NOW,
            6.0,
        )
        assert send is True
        assert 'fingerprint' in reason

    def test_escalation_breaks_cooldown(self):
        fp = build_fingerprint([_sig('inbound_null_bh', 'critical')])
        send, reason = should_send_alert(
            SEVERITY_CRITICAL,
            fp,
            SEVERITY_WARNING,
            fp.replace('critical', 'warning'),
            NOW - timedelta(minutes=10),
            NOW,
            6.0,
        )
        assert send is True
        assert 'escalated' in reason

    def test_cooldown_elapsed_resends(self):
        fp = build_fingerprint([_sig('inbound_null_bh', 'warning')])
        send, reason = should_send_alert(
            SEVERITY_WARNING,
            fp,
            SEVERITY_WARNING,
            fp,
            NOW - timedelta(hours=7),
            NOW,
            6.0,
        )
        assert send is True
        assert 'cooldown elapsed' in reason


@pytest.fixture
def mock_config():
    with patch('models.VettingConfig') as cfg:
        cfg.get_value.side_effect = lambda key, default=None: {
            'ops_early_warning_email': '',
            'health_alert_email': 'kroots@myticas.com',
            'vetting_enabled': 'true',
        }.get(key, default)
        yield cfg


def _run_check(mock_config, *, signals, enabled=True, last_severity=None,
               last_fingerprint=None, last_sent_at=None, send_ok=True):
    with patch('services.ops_early_warning._config_bool', return_value=enabled), \
         patch('services.ops_early_warning._config_float', side_effect=lambda k, d: d), \
         patch('services.ops_early_warning._read_state',
               return_value=(last_severity, last_fingerprint, last_sent_at)), \
         patch('services.ops_early_warning.collect_signals', return_value=signals), \
         patch('services.ops_early_warning._send_alert_email',
               return_value=send_ok) as mock_send:
        result = run_ops_early_warning_check(now=NOW)
    return result, mock_send


class TestOrchestration:
    def test_disabled_short_circuits(self, mock_config):
        result, mock_send = _run_check(
            mock_config,
            signals=[_sig('inbound_null_bh', 'critical')],
            enabled=False,
        )
        assert result['evaluated'] is False
        assert result['alert_sent'] is False
        mock_send.assert_not_called()

    def test_quiet_sends_nothing(self, mock_config):
        result, mock_send = _run_check(
            mock_config,
            signals=[_sig('inbound_null_bh', SEVERITY_NONE)],
        )
        assert result['severity'] == SEVERITY_NONE
        assert result['alert_sent'] is False
        mock_send.assert_not_called()

    def test_critical_sends_and_stamps(self, mock_config):
        signals = [_sig('inbound_null_bh', 'critical', title='Inbound → Bullhorn write rate')]
        result, mock_send = _run_check(mock_config, signals=signals)
        assert result['severity'] == SEVERITY_CRITICAL
        assert result['alert_sent'] is True
        mock_send.assert_called_once()
        recipient, subject, _html = mock_send.call_args[0]
        assert recipient == 'kroots@myticas.com'
        assert 'CRITICAL' in subject

        stamped = {c.args[0] for c in mock_config.set_value.call_args_list}
        assert 'ops_early_warning_last_sent_at' in stamped
        assert 'ops_early_warning_last_severity' in stamped
        assert 'ops_early_warning_last_fingerprint' in stamped

    def test_failed_send_does_not_stamp_alert_state(self, mock_config):
        signals = [_sig('inbound_null_bh', 'critical')]
        result, _ = _run_check(mock_config, signals=signals, send_ok=False)
        assert result['alert_sent'] is False
        assert result['reason'] == 'send failed'
        stamped = {c.args[0] for c in mock_config.set_value.call_args_list}
        # Summary may still stamp; alert state must not.
        assert 'ops_early_warning_last_sent_at' not in stamped

    def test_repeat_same_fingerprint_suppressed(self, mock_config):
        signals = [_sig('inbound_null_bh', 'critical')]
        fp = build_fingerprint(signals)
        result, mock_send = _run_check(
            mock_config,
            signals=signals,
            last_severity=SEVERITY_CRITICAL,
            last_fingerprint=fp,
            last_sent_at=NOW - timedelta(hours=1),
        )
        assert result['alert_sent'] is False
        mock_send.assert_not_called()
