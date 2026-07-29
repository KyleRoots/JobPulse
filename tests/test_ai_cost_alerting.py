"""Regression tests for scheduled OpenAI spend alerting.

Bug history (2026-07-29): `AdminHealthService.tile_ai_cost_24h` already
classified 24h spend as amber/red, but only into an HTTP response. Two runaway
screening loops burned roughly $6.4k/month for six days and alerted nobody
because every reader of `openai_call_log` required a human to load a page.

These tests pin the behaviour that closes that gap: spend is classified on a
schedule, alerts are e-mailed, repeats are suppressed by a cooldown, and an
escalation to critical always breaks through.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from services.ai_cost_monitor import (
    SEVERITY_CRITICAL,
    SEVERITY_NONE,
    SEVERITY_WARNING,
    classify_severity,
    run_ai_cost_alert_check,
    should_send_alert,
)

NOW = datetime(2026, 7, 29, 18, 0, 0)


class TestClassifySeverity:
    def test_below_warning_is_none(self):
        assert classify_severity(79.99, 150.0, 250.0) == SEVERITY_NONE

    def test_at_warning_boundary_is_warning(self):
        assert classify_severity(150.0, 150.0, 250.0) == SEVERITY_WARNING

    def test_between_bands_is_warning(self):
        assert classify_severity(220.22, 150.0, 250.0) == SEVERITY_WARNING

    def test_at_critical_boundary_is_critical(self):
        assert classify_severity(250.0, 150.0, 250.0) == SEVERITY_CRITICAL

    def test_july_28_peak_would_have_been_critical(self):
        """The worst pre-fix day was $335.21. It must trip critical."""
        assert classify_severity(335.21, 150.0, 250.0) == SEVERITY_CRITICAL

    def test_clean_busy_day_stays_quiet(self):
        """A clean busy day (~$87) must not alert, or the signal is worthless."""
        assert classify_severity(87.0, 150.0, 250.0) == SEVERITY_NONE


class TestShouldSendAlert:
    def test_no_alert_when_severity_none(self):
        send, reason = should_send_alert(
            SEVERITY_NONE, None, None, NOW, 6.0
        )
        assert send is False
        assert 'below warning' in reason

    def test_sends_when_no_prior_alert(self):
        send, reason = should_send_alert(
            SEVERITY_WARNING, None, None, NOW, 6.0
        )
        assert send is True
        assert 'no prior alert' in reason

    def test_suppressed_inside_cooldown(self):
        send, reason = should_send_alert(
            SEVERITY_WARNING,
            SEVERITY_WARNING,
            NOW - timedelta(hours=2),
            NOW,
            6.0,
        )
        assert send is False
        assert 'cooldown' in reason

    def test_sends_after_cooldown_elapses(self):
        send, reason = should_send_alert(
            SEVERITY_WARNING,
            SEVERITY_WARNING,
            NOW - timedelta(hours=7),
            NOW,
            6.0,
        )
        assert send is True
        assert 'cooldown elapsed' in reason

    def test_escalation_to_critical_breaks_cooldown(self):
        """A warning 10 minutes ago must not mute a critical."""
        send, reason = should_send_alert(
            SEVERITY_CRITICAL,
            SEVERITY_WARNING,
            NOW - timedelta(minutes=10),
            NOW,
            6.0,
        )
        assert send is True
        assert 'escalated' in reason

    def test_de_escalation_still_respects_cooldown(self):
        send, reason = should_send_alert(
            SEVERITY_WARNING,
            SEVERITY_CRITICAL,
            NOW - timedelta(minutes=10),
            NOW,
            6.0,
        )
        assert send is False
        assert 'cooldown' in reason


@pytest.fixture
def mock_config():
    """Patch every DB touchpoint so the orchestrator runs without a database."""
    with patch('models.VettingConfig') as cfg:
        cfg.get_value.side_effect = lambda key, default=None: {
            'ai_cost_alert_email': '',
            'health_alert_email': 'ops@example.com',
        }.get(key, default)
        yield cfg


def _run(
    mock_config,
    *,
    total_usd,
    enabled=True,
    last_severity=None,
    last_sent_at=None,
    send_ok=True,
    calls_per_run=9.0,
    warn=150.0,
    critical=250.0,
):
    floats = {
        'ai_cost_alert_warn_usd_24h': warn,
        'ai_cost_alert_critical_usd_24h': critical,
        'ai_cost_alert_cooldown_hours': 6.0,
    }
    with patch('services.ai_cost_monitor._config_bool', return_value=enabled), \
         patch('services.ai_cost_monitor._config_float',
               side_effect=lambda k, d: floats.get(k, d)), \
         patch('services.ai_cost_monitor._read_state',
               return_value=(last_severity, last_sent_at)), \
         patch('services.ai_cost_monitor._fetch_spend_window',
               return_value={'total_usd': total_usd, 'calls': 12000}), \
         patch('services.ai_cost_monitor._fetch_top_sites',
               return_value=[{'site': 'screening.scoring', 'calls': 9000,
                              'cost': total_usd * 0.9}]), \
         patch('services.ai_cost_monitor._fetch_calls_per_run',
               return_value=calls_per_run), \
         patch('services.ai_cost_monitor._send_alert_email',
               return_value=send_ok) as mock_send:
        result = run_ai_cost_alert_check(now=NOW)
    return result, mock_send


class TestOrchestration:
    def test_disabled_short_circuits_before_querying(self, mock_config):
        result, mock_send = _run(mock_config, total_usd=999.0, enabled=False)
        assert result['evaluated'] is False
        assert result['alert_sent'] is False
        mock_send.assert_not_called()

    def test_quiet_day_sends_nothing(self, mock_config):
        result, mock_send = _run(mock_config, total_usd=87.0)
        assert result['severity'] == SEVERITY_NONE
        assert result['alert_sent'] is False
        mock_send.assert_not_called()

    def test_critical_spend_sends_and_stamps_state(self, mock_config):
        result, mock_send = _run(mock_config, total_usd=335.21)
        assert result['severity'] == SEVERITY_CRITICAL
        assert result['alert_sent'] is True
        mock_send.assert_called_once()

        recipient, subject, _html = mock_send.call_args[0]
        assert recipient == 'ops@example.com'
        assert 'CRITICAL' in subject

        stamped = {c.args[0] for c in mock_config.set_value.call_args_list}
        assert 'ai_cost_alert_last_sent_at' in stamped
        assert 'ai_cost_alert_last_severity' in stamped

    def test_failed_send_does_not_stamp_state(self, mock_config):
        """A dropped e-mail must retry next cycle, not be silently swallowed."""
        result, _ = _run(mock_config, total_usd=335.21, send_ok=False)
        assert result['alert_sent'] is False
        assert result['reason'] == 'send failed'
        mock_config.set_value.assert_not_called()

    def test_repeat_inside_cooldown_is_suppressed(self, mock_config):
        result, mock_send = _run(
            mock_config,
            total_usd=300.0,
            last_severity=SEVERITY_CRITICAL,
            last_sent_at=NOW - timedelta(hours=1),
        )
        assert result['alert_sent'] is False
        mock_send.assert_not_called()

    def test_recovery_clears_prior_severity(self, mock_config):
        result, mock_send = _run(
            mock_config,
            total_usd=42.0,
            last_severity=SEVERITY_CRITICAL,
            last_sent_at=NOW - timedelta(hours=1),
        )
        assert result['severity'] == SEVERITY_NONE
        mock_send.assert_not_called()
        mock_config.set_value.assert_called_once_with(
            'ai_cost_alert_last_severity', SEVERITY_NONE
        )

    def test_loop_signature_is_called_out_in_the_email(self, mock_config):
        """A 46:1 ratio is the Jul 24 loop. The e-mail must name it."""
        _, mock_send = _run(mock_config, total_usd=300.0, calls_per_run=46.8)
        _recipient, _subject, html = mock_send.call_args[0]
        assert 'Loop signature detected' in html
        assert '46.8' in html

    def test_volume_driven_spike_is_not_called_a_loop(self, mock_config):
        _, mock_send = _run(mock_config, total_usd=300.0, calls_per_run=9.6)
        _recipient, _subject, html = mock_send.call_args[0]
        assert 'Loop signature detected' not in html
        assert 'applicant volume' in html

    def test_inverted_thresholds_are_reordered(self, mock_config):
        """Misconfiguration must not make the warning band unreachable."""
        result, _ = _run(
            mock_config, total_usd=200.0, warn=250.0, critical=150.0
        )
        assert result['severity'] == SEVERITY_WARNING

    def test_missing_recipient_does_not_raise(self, mock_config):
        mock_config.get_value.side_effect = lambda key, default=None: default
        result, mock_send = _run(mock_config, total_usd=335.21)
        assert result['alert_sent'] is False
        assert result['reason'] == 'no recipient configured'
        mock_send.assert_not_called()

    def test_query_failure_is_fail_soft(self, mock_config):
        """A monitoring fault must never take down the scheduler."""
        with patch('services.ai_cost_monitor._config_bool', return_value=True), \
             patch('services.ai_cost_monitor._config_float',
                   side_effect=lambda k, d: d), \
             patch('services.ai_cost_monitor._fetch_spend_window',
                   side_effect=RuntimeError('db gone')):
            result = run_ai_cost_alert_check(now=NOW)
        assert result['alert_sent'] is False
        assert result['reason'].startswith('error:')


class TestNoScreeningActivity:
    def test_none_ratio_does_not_crash_the_email(self, mock_config):
        _, mock_send = _run(mock_config, total_usd=300.0, calls_per_run=None)
        _recipient, _subject, html = mock_send.call_args[0]
        assert 'not meaningful' in html
