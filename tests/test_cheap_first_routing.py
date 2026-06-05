"""Tests for the mini-model "cheap-first with escalation" scoring router.

These are pure-function tests — no app context or DB needed. They lock in the
safety contract validated 2026-06-05 (n=6,302):

  * 'off'      → behaviour is byte-identical to the legacy two-sided range.
  * 'enforce'  → mini < reject_threshold is a cheap reject (skip gpt-5.4);
                 everyone at/above the threshold is escalated to gpt-5.4.
  * 'canary'   → same gate is computed, but gpt-5.4 ALWAYS runs (nobody is
                 dropped) so a live false-negative can be detected first.

The router is ONE-SIDED on purpose: a generous mini score can never auto-qualify
a candidate — gpt-5.4 stays the sole authority on anyone with potential. It also
escalates at min(reject_threshold, job_threshold) so a candidate is never
cheap-rejected if they could possibly qualify.
"""
from candidate_vetting_service.config import VettingConfigMixin


class _Cfg(VettingConfigMixin):
    """Minimal harness exposing the pure helpers without DB/OpenAI init."""

    def __init__(self, config=None):
        self._config = config or {}

    def get_config_value(self, key, default=None):
        return self._config.get(key, default)


# Default legacy range, validated reject threshold, and a typical job threshold.
RANGE = (60.0, 85.0)
MINI = 'gpt-4.1-mini'
JOB_THR = 80.0  # the usual qualification bar; well above the reject threshold


def route(cfg, score, mode, reject=40, rng=RANGE, model=MINI, floor=JOB_THR):
    return cfg._cheap_first_route(score, mode, reject, rng, model, floor)


# --- 'off' mode: legacy two-sided range, gate must stay None ----------------

def test_off_mode_escalates_only_inside_range():
    cfg = _Cfg()
    # Below the range -> no escalation (legacy reject on mini score).
    do, gate = route(cfg, 40, 'off')
    assert do is False and gate is None
    # Inside the range -> escalate (legacy behaviour).
    do, gate = route(cfg, 70, 'off')
    assert do is True and gate is None
    # Above the range -> NOT escalated under legacy rules (unchanged).
    do, gate = route(cfg, 95, 'off')
    assert do is False and gate is None


def test_off_mode_boundaries_inclusive():
    cfg = _Cfg()
    assert route(cfg, 60, 'off')[0] is True
    assert route(cfg, 85, 'off')[0] is True
    assert route(cfg, 59.9, 'off')[0] is False
    assert route(cfg, 85.1, 'off')[0] is False


# --- flagship Layer 2: nothing to escalate, regardless of mode --------------

def test_flagship_layer2_never_escalates():
    cfg = _Cfg()
    for mode in ('off', 'canary', 'enforce'):
        do, gate = route(cfg, 10, mode, model='gpt-5.4')
        assert do is False and gate is None


# --- 'enforce' mode: one-sided gate ----------------------------------------

def test_enforce_rejects_below_threshold():
    cfg = _Cfg()
    do, gate = route(cfg, 39, 'enforce')
    assert do is False
    assert gate == {'decision': 'reject', 'mode': 'enforce', 'mini_score': 39}


def test_enforce_escalates_at_and_above_threshold():
    cfg = _Cfg()
    # Exactly at the threshold escalates (>=).
    do, gate = route(cfg, 40, 'enforce')
    assert do is True and gate['decision'] == 'escalate'
    # A high mini score is STILL escalated (one-sided) so it can never
    # auto-qualify on mini alone.
    do, gate = route(cfg, 95, 'enforce')
    assert do is True and gate['decision'] == 'escalate'


def test_enforce_never_cheap_qualifies_when_job_threshold_below_reject():
    """Safety invariant: if a job threshold is set BELOW the reject threshold,
    a sub-reject mini score that could still qualify must be escalated to
    gpt-5.4 — never cheap-rejected (which would otherwise mark it qualified
    without gpt-5.4 ever seeing it)."""
    cfg = _Cfg()
    # qualify_floor=35 < reject=40; mini=39 would qualify (39 >= 35) -> must escalate.
    do, gate = route(cfg, 39, 'enforce', reject=40, floor=35)
    assert do is True and gate['decision'] == 'escalate'
    # Truly below both -> safe cheap reject.
    do, gate = route(cfg, 34, 'enforce', reject=40, floor=35)
    assert do is False and gate['decision'] == 'reject'


def test_qualify_floor_subtracts_prestige_boost_for_eligible_jobs():
    """A boost-eligible job lowers the qualify floor by the prestige boost, so a
    candidate who could be boosted over the bar is escalated to gpt-5.4 rather
    than cheap-rejected (closes the prestige-boost false-positive path)."""
    cfg = _Cfg()
    # Non-eligible: floor is the raw threshold.
    assert cfg._cheap_first_qualify_floor(80.0, False, 5) == 80.0
    # Eligible: floor drops by the boost headroom.
    assert cfg._cheap_first_qualify_floor(80.0, True, 5) == 75.0


def test_enforce_escalates_boost_crossable_candidate():
    """mini below the raw threshold but within prestige-boost headroom of a
    boost-eligible job must escalate (it could end up qualified after the +5)."""
    cfg = _Cfg()
    # job_threshold=38, reject=40, boost=5 -> qualify_floor=33.
    floor = _Cfg()._cheap_first_qualify_floor(38.0, True, 5)  # 33.0
    # mini=35 < reject(40) and < raw threshold(38) but boostable (35+5=40>=38) -> escalate.
    do, gate = route(cfg, 35, 'enforce', reject=40, floor=floor)
    assert do is True and gate['decision'] == 'escalate'
    # mini=30 can't reach 38 even with +5 (30+5=35<38) -> safe cheap reject.
    do, gate = route(cfg, 30, 'enforce', reject=40, floor=floor)
    assert do is False and gate['decision'] == 'reject'


# --- 'canary' mode: gate computed, gpt-5.4 always runs -----------------------

def test_canary_escalates_even_below_threshold():
    cfg = _Cfg()
    # Would-be reject still escalates in canary (never drops a candidate).
    do, gate = route(cfg, 20, 'canary')
    assert do is True
    assert gate['decision'] == 'canary_check' and gate['mode'] == 'canary'


def test_canary_above_threshold_is_normal_escalate():
    cfg = _Cfg()
    do, gate = route(cfg, 70, 'canary')
    assert do is True and gate['decision'] == 'escalate'


# --- escalation-failure authority policy ------------------------------------

def test_escalation_failure_suppresses_mini_in_cheap_first_modes():
    """If gpt-5.4 escalation FAILS, mini must not be allowed to qualify in
    canary/enforce — gpt-5.4 is the sole authority. In legacy 'off' mode the
    existing fail-soft (keep mini) is preserved so 'off' stays a no-op."""
    cfg = _Cfg()
    # off mode: gate is None -> keep mini (legacy fail-soft).
    assert cfg._suppress_mini_on_escalation_failure(None) is False
    # canary/enforce gates -> suppress mini (re-raise, never qualify on mini).
    for gate in (
        {'decision': 'escalate', 'mode': 'enforce'},
        {'decision': 'escalate', 'mode': 'canary'},
        {'decision': 'canary_check', 'mode': 'canary'},
        {'decision': 'reject', 'mode': 'enforce'},
    ):
        assert cfg._suppress_mini_on_escalation_failure(gate) is True


# --- config parsing: mode + threshold --------------------------------------

def test_routing_mode_defaults_and_normalizes():
    assert _Cfg()._get_screening_routing_mode() == 'off'
    assert _Cfg({'screening_routing_mode': 'ENFORCE'})._get_screening_routing_mode() == 'enforce'
    assert _Cfg({'screening_routing_mode': ' Canary '})._get_screening_routing_mode() == 'canary'
    # Unknown values fall back to the safe 'off'.
    assert _Cfg({'screening_routing_mode': 'bogus'})._get_screening_routing_mode() == 'off'


def test_reject_threshold_default_and_override():
    assert _Cfg()._get_cheap_first_reject_threshold() == 40.0
    assert _Cfg({'cheap_first_reject_threshold': '35'})._get_cheap_first_reject_threshold() == 35.0
    # Garbage falls back to the validated default.
    assert _Cfg({'cheap_first_reject_threshold': 'oops'})._get_cheap_first_reject_threshold() == 40.0
