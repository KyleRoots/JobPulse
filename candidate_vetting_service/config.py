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
        """Get or create Bullhorn service with current credentials"""
        if self.bullhorn:
            return self.bullhorn

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

    def get_config_value(self, key: str, default: str = None) -> str:
        """Get configuration value from database"""
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

    def get_job_threshold(self, job_id: int) -> float:
        """Get match threshold for a specific job (returns job-specific if set, otherwise global default)"""
        try:
            job_req = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
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

    def _get_job_custom_requirements(self, job_id: int) -> Optional[str]:
        """Get custom requirements for a job if user has specified any"""
        try:
            job_req = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
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
