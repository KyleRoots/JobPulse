"""
Tests for Per-Brand Screening Profiles (Task #101).

Covers four layers:
  1. The standard ('standard'/default) system prompt is byte-for-byte unchanged —
     the hard Myticas invariant.
  2. The 'light_industrial' profile applies ONLY two targeted swaps (Rule 13b +
     the FRESH_GRAD/ENTRY hard-cap line) and nothing else.
  3. enforce_experience_floor raises the entry cap 55 -> 70 only for
     light_industrial; standard stays at 55.
  4. Env-aware config resolution: BullhornEnvironment profile/override accessors
     and VettingConfigMixin.get_config_value override precedence with global
     fallback (default env resolves identically).
"""
import pytest
from unittest.mock import MagicMock, patch

from screening.system_prompt import (
    build_system_message,
    _STANDARD_RULE_13B,
    _LIGHT_INDUSTRIAL_RULE_13B,
    _STANDARD_ENTRY_CAP_LINE,
    _LIGHT_INDUSTRIAL_ENTRY_CAP_LINE,
)
from screening.post_processing import enforce_experience_floor


GLOBAL_REQS = "Must have valid work authorization."


# ── 1. Standard prompt unchanged (Myticas hard invariant) ──────────────────
class TestStandardPromptUnchanged:
    def test_default_equals_explicit_standard(self):
        assert build_system_message(GLOBAL_REQS) == \
            build_system_message(GLOBAL_REQS, profile='standard')

    def test_standard_contains_standard_blocks(self):
        msg = build_system_message(GLOBAL_REQS)
        assert _STANDARD_RULE_13B in msg
        assert _STANDARD_ENTRY_CAP_LINE in msg
        assert "MUST NOT exceed 55." in msg

    def test_standard_omits_light_industrial_text(self):
        msg = build_system_message(GLOBAL_REQS)
        assert "LIGHT-INDUSTRIAL PROFILE" not in msg
        assert "MUST NOT exceed 70" not in msg

    def test_empty_and_none_profile_resolve_to_standard(self):
        std = build_system_message(GLOBAL_REQS, profile='standard')
        assert build_system_message(GLOBAL_REQS, profile='') == std
        assert build_system_message(GLOBAL_REQS, profile=None) == std

    def test_unknown_profile_falls_back_to_standard(self):
        std = build_system_message(GLOBAL_REQS, profile='standard')
        assert build_system_message(GLOBAL_REQS, profile='bogus_profile') == std


# ── 2. light_industrial profile: targeted swaps only ───────────────────────
class TestLightIndustrialPrompt:
    def test_swaps_rule_13b(self):
        msg = build_system_message(GLOBAL_REQS, profile='light_industrial')
        assert _STANDARD_RULE_13B not in msg
        assert _LIGHT_INDUSTRIAL_RULE_13B in msg
        assert "LIGHT-INDUSTRIAL PROFILE" in msg

    def test_raises_entry_cap_line(self):
        msg = build_system_message(GLOBAL_REQS, profile='light_industrial')
        assert _STANDARD_ENTRY_CAP_LINE not in msg
        assert "MUST NOT exceed 70" in msg

    def test_only_targeted_sections_differ(self):
        """Reversing exactly the two swaps must reproduce the standard prompt,
        proving no other text changed."""
        std = build_system_message(GLOBAL_REQS, profile='standard')
        light = build_system_message(GLOBAL_REQS, profile='light_industrial')
        assert light != std
        reconstructed = light.replace(_LIGHT_INDUSTRIAL_RULE_13B, _STANDARD_RULE_13B)
        reconstructed = reconstructed.replace(
            _LIGHT_INDUSTRIAL_ENTRY_CAP_LINE, _STANDARD_ENTRY_CAP_LINE
        )
        assert reconstructed == std


# ── 3. Experience-floor cap by profile ─────────────────────────────────────
class TestExperienceFloorProfile:
    def _result(self, score):
        return {
            'match_score': score,
            'experience_level_classification': {
                'classification': 'ENTRY',
                'highest_role_type': 'PROFESSIONAL',
                'total_professional_years': 1.5,
            },
            'key_requirements': 'Minimum 5 years of experience required',
            'gaps_identified': '',
            'years_analysis': {},
        }

    def test_standard_caps_at_55(self):
        r = self._result(90)
        enforce_experience_floor(r, 1, 'Minimum 5 years of experience', '')
        assert r['match_score'] == 55

    def test_light_industrial_caps_at_70(self):
        r = self._result(90)
        enforce_experience_floor(r, 1, 'Minimum 5 years of experience', '',
                                 profile='light_industrial')
        assert r['match_score'] == 70

    def test_light_industrial_below_cap_untouched(self):
        r = self._result(68)
        enforce_experience_floor(r, 1, 'Minimum 5 years of experience', '',
                                 profile='light_industrial')
        assert r['match_score'] == 68

    def test_standard_between_55_and_70_still_capped(self):
        r = self._result(68)
        enforce_experience_floor(r, 1, 'Minimum 5 years of experience', '')
        assert r['match_score'] == 55


# ── 4a. BullhornEnvironment profile/override accessors ──────────────────────
class TestEnvironmentModelAccessors:
    def test_profile_defaults_to_standard(self, app):
        from models import BullhornEnvironment
        env = BullhornEnvironment()
        assert env.get_screening_profile() == 'standard'
        env.screening_profile = '   '
        assert env.get_screening_profile() == 'standard'
        env.screening_profile = 'light_industrial'
        assert env.get_screening_profile() == 'light_industrial'

    def test_overrides_parse_dict(self, app):
        from models import BullhornEnvironment
        env = BullhornEnvironment()
        assert env.get_screening_overrides() == {}
        env.screening_config_overrides = '{"cheap_first_reject_threshold": "30"}'
        assert env.get_screening_overrides() == {'cheap_first_reject_threshold': '30'}

    def test_overrides_malformed_returns_empty(self, app):
        from models import BullhornEnvironment
        env = BullhornEnvironment()
        env.screening_config_overrides = 'not json'
        assert env.get_screening_overrides() == {}
        env.screening_config_overrides = '["a", "b"]'  # valid JSON, not a dict
        assert env.get_screening_overrides() == {}


# ── 4b. Env-aware config resolution in VettingConfigMixin ──────────────────
class TestConfigResolution:
    def _service(self):
        from candidate_vetting_service import CandidateVettingService
        return CandidateVettingService.__new__(CandidateVettingService)

    def test_default_env_profile_is_standard(self, app):
        svc = self._service()
        svc._environment_cache = None  # default/unresolved env
        assert svc._get_screening_profile() == 'standard'

    def test_env_override_wins_over_global(self, app):
        from models import BullhornEnvironment
        env = BullhornEnvironment(
            screening_profile='light_industrial',
            screening_config_overrides='{"cheap_first_reject_threshold": "30"}',
        )
        svc = self._service()
        svc._environment_cache = env
        assert svc._get_screening_profile() == 'light_industrial'
        # Override key resolves to the override value (never touches VettingConfig).
        assert svc.get_config_value('cheap_first_reject_threshold', '40') == '30'

    def test_non_overridden_key_falls_back_to_global(self, app):
        from models import BullhornEnvironment
        env = BullhornEnvironment(screening_config_overrides='{"only_this": "x"}')
        svc = self._service()
        svc._environment_cache = env
        fake_cfg = MagicMock()
        fake_cfg.setting_value = 'gpt-5.4'
        with patch('candidate_vetting_service.config.VettingConfig') as VC:
            VC.query.filter_by.return_value.first.return_value = fake_cfg
            assert svc.get_config_value('layer2_model', 'default') == 'gpt-5.4'
            VC.query.filter_by.assert_called_once_with(setting_key='layer2_model')

    def test_default_env_uses_global_only_path(self, app):
        svc = self._service()
        svc._environment_cache = None
        with patch('candidate_vetting_service.config.VettingConfig') as VC:
            VC.query.filter_by.return_value.first.return_value = None
            assert svc.get_config_value('missing_key', 'fallback') == 'fallback'
            VC.query.filter_by.assert_called_once_with(setting_key='missing_key')
