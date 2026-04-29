"""
Vetting cycle lock management — prevents overlapping vetting runs.
"""

import logging
from datetime import datetime

from app import db
from models import VettingConfig

logger = logging.getLogger('candidate_vetting_service')


class VettingLockMixin:
    """Mixin providing exclusive lock management for vetting cycles."""

    def _acquire_vetting_lock(self) -> bool:
        """Try to acquire exclusive lock for vetting cycle. Returns True if acquired."""
        try:
            config = VettingConfig.query.filter_by(setting_key='vetting_in_progress').first()
            if config:
                if config.setting_value == 'true':
                    lock_time_config = VettingConfig.query.filter_by(setting_key='vetting_lock_time').first()
                    if lock_time_config and lock_time_config.setting_value:
                        try:
                            lock_time = datetime.fromisoformat(lock_time_config.setting_value)
                            lock_age_minutes = (datetime.utcnow() - lock_time).total_seconds() / 60
                            if lock_age_minutes > 5:
                                logger.warning(f"⚠️ Stale vetting lock detected ({lock_age_minutes:.1f} min old), auto-releasing")
                            else:
                                logger.info("Vetting cycle already in progress, skipping")
                                return False
                        except (ValueError, TypeError) as e:
                            logger.warning(f"⚠️ Invalid lock timestamp, auto-releasing: {e}")
                    else:
                        logger.warning("⚠️ Vetting lock exists without timestamp, auto-releasing")
                config.setting_value = 'true'
            else:
                config = VettingConfig(setting_key='vetting_in_progress', setting_value='true')
                db.session.add(config)

            lock_time_config = VettingConfig.query.filter_by(setting_key='vetting_lock_time').first()
            if lock_time_config:
                lock_time_config.setting_value = datetime.utcnow().isoformat()
            else:
                lock_time_config = VettingConfig(setting_key='vetting_lock_time', setting_value=datetime.utcnow().isoformat())
                db.session.add(lock_time_config)

            db.session.commit()
            return True
        except Exception as e:
            logger.error(f"Error acquiring vetting lock: {str(e)}")
            return False

    def _release_vetting_lock(self):
        """Release the vetting lock"""
        try:
            try:
                db.session.rollback()
            except Exception:
                pass
            config = VettingConfig.query.filter_by(setting_key='vetting_in_progress').first()
            if config:
                config.setting_value = 'false'
                db.session.commit()
        except Exception as e:
            logger.error(f"Error releasing vetting lock: {str(e)}")
            try:
                db.session.rollback()
            except Exception:
                pass
