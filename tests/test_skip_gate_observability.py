"""
Tests for the May 2026 skip-gate observability tile + per-worker counters.

Covers:
  1. _COOLDOWN_BLOCK_COUNTER increments when the self-screen cooldown fires
  2. _RECRUITER_DECISION_BLOCK_COUNTER mutability (mirror of in-place mechanism)
  3. tile_skip_gates green/amber/red status policy and subtext formatting
  4. tile_skip_gates registered in collect_all canonical list
"""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import app
from screening import dedup as _dedup_mod
from screening import note_builder as _nb_mod
from screening.dedup import CandidateDeduplicationMixin
from services.admin_health_service import AdminHealthService


@pytest.fixture(autouse=True)
def _reset_counters():
    _dedup_mod._COOLDOWN_BLOCK_COUNTER = 0
    _dedup_mod._RECRUITER_DECISION_BLOCK_COUNTER = 0
    _nb_mod._DEDUPE_REJECTION_COUNTER = 0
    yield
    _dedup_mod._COOLDOWN_BLOCK_COUNTER = 0
    _dedup_mod._RECRUITER_DECISION_BLOCK_COUNTER = 0
    _nb_mod._DEDUPE_REJECTION_COUNTER = 0


def _mixin():
    class _Stub(CandidateDeduplicationMixin):
        pass
    return _Stub()


# -----------------------------------------------------------------------------
# Counter increment tests
# -----------------------------------------------------------------------------

def test_cooldown_counter_increments_on_block():
    """When the self-screen cooldown blocks a candidate, the module-level
    _COOLDOWN_BLOCK_COUNTER must increment by 1 per block."""
    recent = SimpleNamespace(id=99, created_at=datetime.utcnow() - timedelta(minutes=10))
    with patch.object(_dedup_mod.VettingConfig, 'get_value', return_value='60'), \
         patch.object(_dedup_mod.CandidateVettingLog, 'query') as mq:
        mq.filter.return_value.order_by.return_value.first.return_value = recent
        assert _mixin()._self_screen_cooldown_active(12345) is True
        assert _dedup_mod._COOLDOWN_BLOCK_COUNTER == 1
        assert _mixin()._self_screen_cooldown_active(67890) is True
        assert _dedup_mod._COOLDOWN_BLOCK_COUNTER == 2


def test_cooldown_counter_does_not_increment_when_no_recent():
    with patch.object(_dedup_mod.VettingConfig, 'get_value', return_value='60'), \
         patch.object(_dedup_mod.CandidateVettingLog, 'query') as mq:
        mq.filter.return_value.order_by.return_value.first.return_value = None
        assert _mixin()._self_screen_cooldown_active(12345) is False
        assert _dedup_mod._COOLDOWN_BLOCK_COUNTER == 0


def test_cooldown_counter_does_not_increment_when_killswitch_off():
    with patch.object(_dedup_mod.VettingConfig, 'get_value', return_value='0'):
        assert _mixin()._self_screen_cooldown_active(12345) is False
        assert _dedup_mod._COOLDOWN_BLOCK_COUNTER == 0


def test_recruiter_decision_counter_mutable():
    """Counter starts at 0 and supports in-place increment (mirrors the
    mechanism used inside _is_paused_by_recruiter_decision)."""
    assert _dedup_mod._RECRUITER_DECISION_BLOCK_COUNTER == 0
    _dedup_mod._RECRUITER_DECISION_BLOCK_COUNTER += 1
    _dedup_mod._RECRUITER_DECISION_BLOCK_COUNTER += 1
    assert _dedup_mod._RECRUITER_DECISION_BLOCK_COUNTER == 2


# -----------------------------------------------------------------------------
# tile_skip_gates status policy
# -----------------------------------------------------------------------------

def _vetting_cfg(cooldown='60', recruiter_skip='true'):
    def _side(key):
        if key == 'self_screen_cooldown_minutes':
            return cooldown
        if key == 'recruiter_decision_skip_enabled':
            return recruiter_skip
        return None
    return _side


def test_tile_skip_gates_green_when_enabled_and_low_volume():
    with app.app_context(), \
         patch('models.VettingConfig.get_value', side_effect=_vetting_cfg()):
        _dedup_mod._COOLDOWN_BLOCK_COUNTER = 5
        _dedup_mod._RECRUITER_DECISION_BLOCK_COUNTER = 2
        _nb_mod._DEDUPE_REJECTION_COUNTER = 1
        tile = AdminHealthService().tile_skip_gates()
        assert tile.status == 'green'
        assert tile.value == '8 block(s)'
        assert 'cooldown=5' in tile.subtext
        assert 'recruiter-decision=2' in tile.subtext
        assert 'note-dedupe=1' in tile.subtext
        assert 'cooldown=60min' in tile.subtext
        assert 'recruiter-skip=on' in tile.subtext
        assert tile.remediation == ''


def test_tile_skip_gates_red_when_cooldown_disabled():
    with app.app_context(), \
         patch('models.VettingConfig.get_value', side_effect=_vetting_cfg(cooldown='0')):
        tile = AdminHealthService().tile_skip_gates()
        assert tile.status == 'red'
        assert 'DISABLED' in tile.remediation


def test_tile_skip_gates_amber_when_high_volume():
    with app.app_context(), \
         patch('models.VettingConfig.get_value', side_effect=_vetting_cfg()):
        _dedup_mod._COOLDOWN_BLOCK_COUNTER = 250
        tile = AdminHealthService().tile_skip_gates()
        assert tile.status == 'amber'
        assert 'event=cooldown_blocked' in tile.remediation


def test_tile_skip_gates_recruiter_skip_off_reflected_in_subtext():
    with app.app_context(), \
         patch('models.VettingConfig.get_value',
               side_effect=_vetting_cfg(recruiter_skip='false')):
        tile = AdminHealthService().tile_skip_gates()
        assert 'recruiter-skip=off' in tile.subtext


def test_tile_skip_gates_registered_in_collect_all():
    """Regression: tile must be in the canonical collectors list."""
    with app.app_context(), \
         patch('models.VettingConfig.get_value', side_effect=_vetting_cfg()):
        tiles = AdminHealthService().collect_all()
        keys = [t.key for t in tiles]
        assert 'skip_gates' in keys
