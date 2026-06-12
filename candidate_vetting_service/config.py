"""
Configuration and settings access for the CandidateVettingService.
"""

import logging
import os
from datetime import datetime
from typing import Optional

from openai import OpenAI

from app import db
from models import VettingConfig, GlobalSettings, JobVettingRequirements
from bullhorn_service import BullhornService

logger = logging.getLogger('candidate_vetting_service')


class VettingConfigMixin:
    """Mixin providing configuration access, threshold management,
    and Bullhorn/OpenAI initialization for the vetting service."""

    def _init_openai(self):
        """Initialize OpenAI client"""
        api_key = os.environ.get('OPENAI_API_KEY')
        if api_key:
            self.openai_client = OpenAI(api_key=api_key)
        else:
            logger.warning("OPENAI_API_KEY not found - AI matching will not work")

    def _get_bullhorn_service(self) -> BullhornService:
        """Get or create Bullhorn service for this service's environment.

        Non-default environments that carry their own inline credential set
        connect to their OWN Bullhorn instance. The default (Myticas)
        environment carries no inline credentials, so this falls through to the
        historical GlobalSettings path below — byte-for-byte unchanged.
        """
        if self.bullhorn:
            return self.bullhorn

        try:
            env = self._resolve_environment()
            if env is not None and not getattr(env, 'is_default', False):
                creds = env.resolve_credentials()
                if creds:
                    from utils.bullhorn_helpers import get_bullhorn_service
                    self.bullhorn = get_bullhorn_service(env)
                    return self.bullhorn
                # Non-default tenant WITHOUT a complete inline credential set:
                # fail CLOSED. Falling through to GlobalSettings would run this
                # tenant's cycle against the DEFAULT (Myticas) Bullhorn instance
                # — cross-tenant data corruption. Refuse instead.
                logger.error(
                    "Non-default environment '%s' lacks a complete inline "
                    "Bullhorn credential set; refusing to fall back to global "
                    "credentials.", getattr(env, 'key', getattr(env, 'id', '?'))
                )
                return None
        except Exception as e:
            logger.debug(f"Env-credential resolution skipped (fail-soft to global): {e}")

        bh_keys = ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']
        rows = GlobalSettings.query.filter(GlobalSettings.setting_key.in_(bh_keys)).all()
        raw = {r.setting_key: r.setting_value for r in rows if r.setting_value}
        credentials = {k.replace('bullhorn_', ''): raw[k] for k in bh_keys if k in raw}

        if len(credentials) == 4:
            self.bullhorn = BullhornService(
                client_id=credentials['client_id'],
                client_secret=credentials['client_secret'],
                username=credentials['username'],
                password=credentials['password']
            )
            return self.bullhorn
        else:
            logger.error("Bullhorn credentials not fully configured")
            return None

    def _resolve_environment(self):
        """Lazily resolve and cache this service's BullhornEnvironment row.

        ``self.environment_id`` selects the tenant; ``None`` resolves the default
        (Myticas) environment. Fully fail-soft — any error caches/returns
        ``None`` so config resolution falls back to the historical global path.
        """
        cached = getattr(self, '_environment_cache', 'UNSET')
        if cached != 'UNSET':
            return cached
        env = None
        try:
            from models import BullhornEnvironment
            env_id = getattr(self, 'environment_id', None)
            if env_id is not None:
                env = BullhornEnvironment.query.get(env_id)
            else:
                env = BullhornEnvironment.get_default()
        except Exception as e:
            logger.debug(f"Environment resolution skipped (fail-soft to global): {e}")
            env = None
        self._environment_cache = env
        return env

    def _get_env_screening_overrides(self) -> dict:
        """Return this environment's VettingConfig override map (cached, fail-soft).

        The default environment carries no overrides, so this is ``{}`` and
        config resolution is identical to the historical global-only path.
        """
        cached = getattr(self, '_env_overrides_cache', None)
        if cached is not None:
            return cached
        overrides = {}
        try:
            env = self._resolve_environment()
            if env is not None:
                overrides = env.get_screening_overrides() or {}
        except Exception:
            overrides = {}
        self._env_overrides_cache = overrides
        return overrides

    def _get_screening_profile(self) -> str:
        """Return the screening profile key for this environment (cached).

        Defaults to 'standard' (the historical behavior) for the default
        environment or on any resolution error.
        """
        cached = getattr(self, '_screening_profile_cache', None)
        if cached is not None:
            return cached
        profile = 'standard'
        try:
            env = self._resolve_environment()
            if env is not None:
                profile = env.get_screening_profile() or 'standard'
        except Exception:
            profile = 'standard'
        self._screening_profile_cache = profile
        return profile

    def get_config_value(self, key: str, default: str = None) -> str:
        """Get configuration value, honoring per-environment overrides.

        Resolution order: a per-environment screening override (when present)
        wins over the global VettingConfig row. The default environment has no
        overrides, so this is byte-for-byte the historical global-only lookup.
        """
        overrides = self._get_env_screening_overrides()
        if key in overrides:
            value = overrides[key]
            return value if value is None else str(value)
        config = VettingConfig.query.filter_by(setting_key=key).first()
        return config.setting_value if config else default

    def is_enabled(self) -> bool:
        """Check if vetting is enabled"""
        return self.get_config_value('vetting_enabled', 'false').lower() == 'true'

    def get_threshold(self) -> float:
        """Get global match threshold percentage"""
        try:
            return float(self.get_config_value('match_threshold', '80'))
        except (ValueError, TypeError):
            return 80.0

    def _job_req_for(self, job_id: int):
        """Resolve the JobVettingRequirements row for ``job_id``, scoped to this
        service's environment.

        ``bullhorn_job_id`` is no longer globally unique once a second tenant is
        active, so an unscoped lookup could read another tenant's job settings.
        This filters by ``environment_id`` ONLY when more than one environment is
        active — matching ``scope_query``'s no-op so single-tenant prod (where
        legacy rows may carry a NULL ``environment_id``) is byte-for-byte
        unchanged. Fail-soft: any resolution error falls back to the unscoped
        historical lookup.
        """
        query = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id)
        try:
            from utils.environment_context import _active_environment_count
            if _active_environment_count() > 1:
                env = self._resolve_environment()
                if env is not None:
                    query = query.filter(
                        JobVettingRequirements.environment_id == env.id
                    )
        except Exception:
            pass
        return query.first()

    def get_job_threshold(self, job_id: int) -> float:
        """Get match threshold for a specific job (returns job-specific if set, otherwise global default)"""
        try:
            job_req = self._job_req_for(job_id)
            if job_req and job_req.vetting_threshold is not None:
                return float(job_req.vetting_threshold)
            return self.get_threshold()
        except Exception as e:
            logger.warning(f"Error getting job threshold for {job_id}: {e}")
            return self.get_threshold()

    def _get_layer2_model(self) -> str:
        """Get the Layer 2 model from VettingConfig (supports live revert)."""
        try:
            value = self.get_config_value('layer2_model', 'gpt-5.4')
            if value and value.strip():
                return value.strip()
        except Exception:
            pass
        return 'gpt-5.4'

    def _get_escalation_range(self) -> tuple:
        """Get escalation score range from VettingConfig.

        Returns:
            Tuple of (low, high) — scores within this range trigger GPT-5.4 re-analysis.
        """
        try:
            low = float(self.get_config_value('escalation_low', '60'))
            high = float(self.get_config_value('escalation_high', '85'))
            return (low, high)
        except (ValueError, TypeError):
            return (60.0, 85.0)

    def should_escalate_to_layer3(self, match_score: float) -> bool:
        """Check if a match score falls in the escalation range for Layer 3 re-analysis.

        Args:
            match_score: Layer 2 model match score.

        Returns:
            True if score is within [escalation_low, escalation_high].
        """
        low, high = self._get_escalation_range()
        return low <= match_score <= high

    def _get_screening_routing_mode(self) -> str:
        """Cheap-first routing mode (VettingConfig key 'screening_routing_mode').

        - 'off' (default): legacy two-sided range escalation, unchanged.
        - 'canary': cheap-first gate is computed and logged, but gpt-5.4 is
          STILL run on every candidate (never drops anyone) so we can confirm
          0 live false-negatives before enforcing.
        - 'enforce': below the reject threshold, trust mini's clear reject and
          skip gpt-5.4 (the cost saving). gpt-5.4 still scores everyone at/above
          the threshold and remains the sole authority on qualification.
        """
        try:
            value = (self.get_config_value('screening_routing_mode', 'off') or 'off').strip().lower()
            if value in ('off', 'canary', 'enforce'):
                return value
        except Exception:
            pass
        return 'off'

    def _get_cheap_first_reject_threshold(self) -> float:
        """Layer-2 (mini) score below which cheap-first treats a candidate as a
        clear reject (VettingConfig key 'cheap_first_reject_threshold').

        Validated 2026-06-05 (n=6,302): at 40, 74.5% of scorings route cheap
        with 0/43 gpt-5.4-qualified candidates lost.
        """
        try:
            return float(self.get_config_value('cheap_first_reject_threshold', '40'))
        except (ValueError, TypeError):
            return 40.0

    def _cheap_first_qualify_floor(self, job_threshold, boost_eligible,
                                   prestige_boost_points):
        """Lowest raw mini score that could still end up QUALIFIED after every
        POST-ROUTING adjustment the router must compensate for.

        The prestige boost — applied after routing, in the persistence loop,
        outside gpt-5.4 — is the only such upward adjustment, so a boost-eligible
        job lowers the floor by ``prestige_boost_points``. The other score
        movers are NOT post-routing-outside-gpt-5.4 raises: the remote-location
        fix is applied inside the scoring call (already baked into the mini
        score the router sees), the zero-score reverify re-scores with gpt-5.4
        (the authoritative model), and location barriers only ever lower
        qualification.
        """
        if boost_eligible:
            return job_threshold - prestige_boost_points
        return job_threshold

    def _suppress_mini_on_escalation_failure(self, cheap_gate):
        """Policy: if the gpt-5.4 escalation call FAILS, may we keep the mini
        analysis (which could qualify on mini alone)?

        In cheap-first modes (canary/enforce — ``cheap_gate`` is not None) the
        answer is NO: gpt-5.4 is the sole qualification authority, so a failed
        escalation must NOT produce a mini-only qualified record. The caller
        re-raises instead, recording the job as a failed/zero result (the same
        outcome the system already produces when an authoritative gpt-5.4 call
        fails, and which the zero-score gpt-5.4 reverify can later recover).

        In legacy 'off' mode (``cheap_gate is None``) we preserve the existing
        fail-soft behaviour (keep the mini analysis) so 'off' stays a no-op.
        """
        return cheap_gate is not None

    def _cheap_first_route(self, mini_score, routing_mode, reject_threshold,
                           escalation_range, layer2_model, qualify_floor):
        """Pure routing decision (no DB/IO) — easy to unit test.

        Returns (do_escalate: bool, gate: Optional[dict]). ``gate`` is None in
        legacy 'off' mode; otherwise a dict whose 'decision' is one of
        {'escalate', 'canary_check', 'reject'}.

        Cheap-first is ONE-SIDED on purpose: any candidate at/above the reject
        threshold is escalated to gpt-5.4 (no upper bound), so a generous mini
        score can never auto-qualify a candidate — gpt-5.4 stays the sole
        authority on anyone with potential.

        Safety invariant: a candidate is only cheap-rejected when their mini
        score is below BOTH the reject threshold AND the job's qualification
        floor (``qualify_floor`` already accounts for prestige-boost headroom).
        We escalate at ``min(reject_threshold, qualify_floor)`` so that anyone
        who could possibly qualify — even via a low job threshold or a prestige
        boost — is still scored by gpt-5.4. gpt-5.4 therefore remains the sole
        authority on every qualification, and the cheap-reject path can never
        produce a qualified record.
        """
        esc_low, esc_high = escalation_range
        if layer2_model == 'gpt-5.4':
            return (False, None)
        if routing_mode == 'off':
            return (esc_low <= mini_score <= esc_high, None)
        effective_threshold = min(reject_threshold, qualify_floor)
        if mini_score >= effective_threshold:
            return (True, {'decision': 'escalate', 'mode': routing_mode, 'mini_score': mini_score})
        if routing_mode == 'canary':
            return (True, {'decision': 'canary_check', 'mode': 'canary', 'mini_score': mini_score})
        return (False, {'decision': 'reject', 'mode': 'enforce', 'mini_score': mini_score})

    def _get_job_custom_requirements(self, job_id: int) -> Optional[str]:
        """Get custom requirements for a job if user has specified any"""
        try:
            job_req = self._job_req_for(job_id)
            if job_req:
                return job_req.get_active_requirements()
            return None
        except Exception as e:
            logger.error(f"Error getting custom requirements for job {job_id}: {str(e)}")
            return None

    def _get_global_custom_requirements(self) -> Optional[str]:
        """Get global screening instructions that apply to ALL jobs."""
        try:
            config = VettingConfig.query.filter_by(setting_key='global_custom_requirements').first()
            if config and config.setting_value and config.setting_value.strip():
                return config.setting_value.strip()
            return None
        except Exception as e:
            logger.error(f"Error getting global custom requirements: {str(e)}")
            return None

    def _get_last_run_timestamp(self) -> Optional[datetime]:
        """Get the last successful vetting run timestamp from config"""
        config = VettingConfig.query.filter_by(setting_key='last_run_timestamp').first()
        if config and config.setting_value:
            try:
                return datetime.fromisoformat(config.setting_value)
            except (ValueError, TypeError):
                return None
        return None

    def _set_last_run_timestamp(self, timestamp: datetime):
        """Save the last successful vetting run timestamp"""
        config = VettingConfig.query.filter_by(setting_key='last_run_timestamp').first()
        if config:
            config.setting_value = timestamp.isoformat()
        else:
            config = VettingConfig(setting_key='last_run_timestamp', setting_value=timestamp.isoformat())
            db.session.add(config)
        db.session.commit()

    def _get_admin_notification_email(self) -> str:
        """Get the admin notification email from VettingConfig."""
        try:
            config = VettingConfig.query.filter_by(setting_key='admin_notification_email').first()
            if config and config.setting_value:
                return config.setting_value.strip()
        except Exception:
            pass
        return ''

    def _get_batch_size(self) -> int:
        """Get configured batch size from database, default 25"""
        try:
            config = VettingConfig.query.filter_by(setting_key='batch_size').first()
            if config and config.setting_value:
                batch = int(config.setting_value)
                return max(1, min(batch, 100))
        except (ValueError, TypeError):
            pass
        return 25
