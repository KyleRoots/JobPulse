"""GlobalSettings, VettingConfig, BullhornMonitor, RefreshLog,
RecruiterMapping, EnvironmentStatus, and built-in AutomationTask seeders.

Production-aware defaults come from `seed_database.is_production_environment`
(referenced via late module lookup so test patches apply at call time).
"""

import os
import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def _default_platform_age_ceilings_json():
    """Return the JSON-encoded default PLATFORM_AGE_CEILINGS dict.

    Sourced from vetting_audit_service.DEFAULT_PLATFORM_AGE_CEILINGS so the
    seed default and the service default never drift apart. Falls back to an
    empty JSON object if the import fails (extremely unlikely in practice but
    keeps seed startup robust).
    """
    try:
        from vetting_audit_service import DEFAULT_PLATFORM_AGE_CEILINGS
        return json.dumps(DEFAULT_PLATFORM_AGE_CEILINGS)
    except Exception as e:
        logger.warning(f"⚠️ Failed to load default platform ceilings: {e!r}")
        return '{}'


def _load_global_screening_prompt():
    """
    Load the global screening prompt from the version-controlled config file.
    Falls back to empty string if the file is missing — never crashes the seed process.
    """
    # The prompt file lives next to seed_database.py (project root /config/),
    # not next to this file. Resolve from the project root which is two
    # directories up from this module: seeding/ -> project_root.
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_path = os.path.join(project_root, 'config', 'global_screening_prompt.txt')
    try:
        with open(prompt_path, 'r') as f:
            prompt = f.read().strip()
        if prompt:
            logger.info(f"✅ Loaded global screening prompt from {prompt_path} ({len(prompt)} chars)")
            return prompt
        else:
            logger.warning(f"⚠️ Global screening prompt file exists but is empty: {prompt_path}")
            return ''
    except FileNotFoundError:
        logger.warning(f"⚠️ Global screening prompt file not found: {prompt_path} — seeding empty string")
        return ''
    except Exception as e:
        logger.warning(f"⚠️ Failed to read global screening prompt: {str(e)} — seeding empty string")
        return ''


def seed_global_settings(db, GlobalSettings):
    """
    Seed global settings including SFTP config, Bullhorn credentials, and automation toggles

    Args:
        db: SQLAlchemy database instance
        GlobalSettings: GlobalSettings model class
    """
    # Late import so test patches on seed_database.is_production_environment /
    # seed_database.get_sftp_config / seed_database.get_bullhorn_config apply.
    import seed_database

    try:
        sftp_config = seed_database.get_sftp_config()
        bullhorn_config = seed_database.get_bullhorn_config()

        # In production, validate that required Bullhorn credentials are present
        if seed_database.is_production_environment():
            missing_bullhorn = []
            for key in ['bullhorn_client_id', 'bullhorn_client_secret', 'bullhorn_username', 'bullhorn_password']:
                if not bullhorn_config.get(key):
                    missing_bullhorn.append(key.upper())

            if missing_bullhorn:
                error_msg = f"PRODUCTION DEPLOYMENT ERROR: Missing required Bullhorn secrets: {', '.join(missing_bullhorn)}"
                logger.error(f"❌ {error_msg}")
                raise ValueError(error_msg)

            # Validate SFTP credentials for production automation
            missing_sftp = []
            for key in ['sftp_hostname', 'sftp_username', 'sftp_password']:
                if not sftp_config.get(key):
                    missing_sftp.append(key.upper())

            if missing_sftp:
                error_msg = f"PRODUCTION DEPLOYMENT ERROR: Missing required SFTP secrets: {', '.join(missing_sftp)}"
                logger.error(f"❌ {error_msg}")
                raise ValueError(error_msg)

        # Define credential settings to seed (toggles handled separately to preserve user settings)
        credential_settings = {
            # SFTP Configuration
            'sftp_hostname': sftp_config['sftp_hostname'],
            'sftp_username': sftp_config['sftp_username'],
            'sftp_password': sftp_config['sftp_password'],
            'sftp_port': sftp_config['sftp_port'],
            'sftp_directory': sftp_config['sftp_directory'],

            # Bullhorn API Configuration
            'bullhorn_client_id': bullhorn_config['bullhorn_client_id'],
            'bullhorn_client_secret': bullhorn_config['bullhorn_client_secret'],
            'bullhorn_username': bullhorn_config['bullhorn_username'],
            'bullhorn_password': bullhorn_config['bullhorn_password']
        }

        settings_created = []

        # Process credential settings
        for key, value in credential_settings.items():
            existing = GlobalSettings.query.filter_by(setting_key=key).first()

            if existing:
                # PRESERVE existing credentials — NEVER overwrite values managed via the UI.
                # Production credentials are the source of truth; env vars only seed first-run
                # rows. This prevents a deployment from resetting credentials that an operator
                # has updated through the UI after the initial bootstrap.
                logger.info(f"ℹ️ Preserving credential setting: {key}")
                continue
            else:
                # Create new credential setting only if value exists (first-run bootstrap only)
                if value:
                    new_setting = GlobalSettings(
                        setting_key=key,
                        setting_value=value,
                        created_at=datetime.utcnow()
                    )
                    db.session.add(new_setting)
                    settings_created.append(key)

        # Process automation toggles separately - NEVER overwrite existing values
        # Defaults are always conservative (off) — user enables via UI.
        # automated_uploads_enabled default flipped from 'true' to 'false' on
        # 2026-04-25 to match the documented "always conservative" contract and
        # prevent fresh-install Repls from auto-uploading SFTP feeds before the
        # operator has reviewed credentials. Existing prod values are preserved
        # by the existence check below; this only changes first-run behavior.
        automation_toggles = {
            'sftp_enabled': 'false',
            'automated_uploads_enabled': 'false',
            'candidate_cleanup_enabled': 'false',
            'candidate_cleanup_batch_size': '50',
        }

        for key, default_value in automation_toggles.items():
            existing = GlobalSettings.query.filter_by(setting_key=key).first()

            if existing:
                # PRESERVE user settings - NEVER overwrite existing toggle values
                # User controls these via UI, we only set defaults on first creation
                logger.info(f"ℹ️ Preserving user setting: {key}={existing.setting_value}")
                continue
            else:
                # Create new toggle with production-aware default (first run only)
                # Re-evaluate is_production_environment() at creation time for correctness
                new_setting = GlobalSettings(
                    setting_key=key,
                    setting_value=default_value,
                    created_at=datetime.utcnow()
                )
                db.session.add(new_setting)
                settings_created.append(key)
                logger.info(f"✅ Created automation toggle: {key}={default_value} (production={seed_database.is_production_environment()})")

        if settings_created:
            db.session.commit()
            logger.info(f"✅ Created settings: {', '.join(settings_created)}")
        else:
            logger.info("✅ Global settings already up to date")

    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed global settings: {str(e)}")
        raise


def seed_bullhorn_monitors(db, BullhornMonitor):
    """
    Seed BullhornMonitor tearsheet configurations (upsert behavior)

    Args:
        db: SQLAlchemy database instance
        BullhornMonitor: BullhornMonitor model class
    """
    try:
        # Define tearsheet configurations (Bullhorn One IDs - January 2026 migration)
        tearsheet_configs = [
            {
                'name': 'Sponsored - OTT',
                'tearsheet_id': 1231,
                'tearsheet_name': 'Sponsored - OTT',
                'notification_email': 'apply@myticas.com'
            },
            {
                'name': 'Sponsored - CHI',
                'tearsheet_id': 1232,
                'tearsheet_name': 'Sponsored - CHI',
                'notification_email': 'apply@myticas.com'
            },
            {
                'name': 'Sponsored - CLE',
                'tearsheet_id': 1233,
                'tearsheet_name': 'Sponsored - CLE',
                'notification_email': 'apply@myticas.com'
            },
            {
                'name': 'Sponsored - VMS',
                'tearsheet_id': 1239,
                'tearsheet_name': 'Sponsored - VMS',
                'notification_email': 'apply@myticas.com'
            },
            {
                'name': 'Sponsored - GR',
                'tearsheet_id': 1474,
                'tearsheet_name': 'Sponsored - GR',
                'notification_email': 'apply@myticas.com'
            },
            {
                'name': 'Sponsored - STSI',
                'tearsheet_id': 1531,
                'tearsheet_name': 'Sponsored - STSI',
                'notification_email': ''  # No email for STSI
            }
        ]

        monitors_created = []
        monitors_updated = []

        for config in tearsheet_configs:
            # Check if monitor already exists
            existing = BullhornMonitor.query.filter_by(
                tearsheet_id=config['tearsheet_id']
            ).first()

            if existing:
                # Update existing monitor to ensure correct configuration
                updates_made = []

                if existing.name != config['name']:
                    existing.name = config['name']
                    updates_made.append('name')

                if existing.tearsheet_name != config['tearsheet_name']:
                    existing.tearsheet_name = config['tearsheet_name']
                    updates_made.append('tearsheet_name')

                if not existing.is_active:
                    existing.is_active = True
                    updates_made.append('is_active')

                if existing.check_interval_minutes != 5:
                    existing.check_interval_minutes = 5
                    updates_made.append('check_interval_minutes')

                if not existing.send_notifications:
                    existing.send_notifications = True
                    updates_made.append('send_notifications')

                # Update notification email if different
                if existing.notification_email != config['notification_email']:
                    existing.notification_email = config['notification_email']
                    updates_made.append('notification_email')

                if updates_made:
                    existing.updated_at = datetime.utcnow()
                    monitors_updated.append(f"{config['name']} ({', '.join(updates_made)})")
            else:
                # Create new monitor
                new_monitor = BullhornMonitor(
                    name=config['name'],
                    tearsheet_id=config['tearsheet_id'],
                    tearsheet_name=config['tearsheet_name'],
                    is_active=True,
                    check_interval_minutes=5,
                    notification_email=config['notification_email'],
                    send_notifications=True,
                    next_check=datetime.utcnow(),
                    created_at=datetime.utcnow()
                )
                db.session.add(new_monitor)
                monitors_created.append(config['name'])

        if monitors_created or monitors_updated:
            db.session.commit()

            if monitors_created:
                logger.info(f"✅ Created {len(monitors_created)} Bullhorn monitors: {', '.join(monitors_created)}")

            if monitors_updated:
                logger.info(f"🔄 Updated {len(monitors_updated)} Bullhorn monitors to production defaults")
        else:
            logger.info(f"✅ All 5 Bullhorn monitors already configured correctly")

    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed Bullhorn monitors: {str(e)}")
        raise


def seed_vetting_config(db, VettingConfig):
    """
    Seed AI Vetting configuration settings

    In production, vetting is ENABLED by default (vetting_enabled=true)
    In development, vetting is DISABLED by default (vetting_enabled=false)

    Like automation toggles, user settings are preserved - we only set defaults on first creation.

    Args:
        db: SQLAlchemy database instance
        VettingConfig: VettingConfig model class
    """
    # Late import so test patches on seed_database.is_production_environment apply.
    import seed_database

    is_prod = seed_database.is_production_environment()

    try:
        # Define vetting settings with production-aware defaults
        # IMPORTANT: Every VettingConfig key that the app reads MUST be listed here
        # so it is recreated with a sensible default if the DB is ever reset.
        # Production defaults reflect actual operational values; dev defaults are
        # conservative to prevent accidental emails or processing.
        # Defaults are always conservative — user configures via UI.
        # No environment-conditional values for operational toggles.
        vetting_settings = {
            'vetting_enabled': 'false',           # Always off by default; user enables via UI
            'match_threshold': '80',
            'batch_size': '25',                    # Default batch size per 5-minute cycle
            'admin_notification_email': 'kroots@myticas.com',
            'health_alert_email': 'kroots@myticas.com',
            'send_recruiter_emails': 'false',      # Always off by default; user enables via UI
            # Embedding pre-filter settings (Layer 1)
            'embedding_filter_enabled': 'true',    # Killswitch: set to 'false' to bypass filter
            'embedding_similarity_threshold': '0.25',  # Conservative default; user tightens via UI
            # Escalation settings (Layer 3)
            'escalation_low': '60',
            'escalation_high': '85',
            # Layer 2 model (revertible via UI if quality drop detected)
            'layer2_model': 'gpt-5.4',
            # Cutoff date: only process candidates received after this timestamp
            'vetting_cutoff_date': '',             # Empty by default; user sets via UI
            # Screening quality audit
            'screening_audit_enabled': 'false',    # Off by default; user enables via UI
            # Quality auditor model — separately configurable from layer2_model
            # so an admin can run the auditor on a stronger/cheaper model
            # without affecting primary screening.
            'quality_auditor_model': 'gpt-5.4',
            # Platform-age ceilings (years) used by the heuristic pre-checks
            # to detect impossible "X years required" requirements when the
            # platform itself is younger than X. JSON-encoded dict of
            # platform_name (lowercase) -> max_years_float.
            'platform_age_ceilings': _default_platform_age_ceilings_json(),
            # Sample rate (percent, 0-100) for the new Qualified false-positive
            # audit phase. 0 disables it; 10 means 10% of recent Qualified
            # results are sampled per cycle.
            'qualified_audit_sample_rate': '10',
            # Recruiter-activity gate (Task D) — pause auto-vet when a human
            # recruiter has touched the candidate within the lookback window.
            # Killswitch and tunable window so admins can adjust without a deploy.
            'recruiter_activity_check_enabled': 'true',
            'recruiter_activity_lookback_minutes': '60',
            # Global screening instructions — loaded from version-controlled config file
            # so a DB reset restores the full prompt instead of wiping it.
            'global_custom_requirements': _load_global_screening_prompt(),
        }

        settings_created = []

        for key, default_value in vetting_settings.items():
            existing = VettingConfig.query.filter_by(setting_key=key).first()

            if existing:
                # PRESERVE user settings - NEVER overwrite existing values
                logger.info(f"ℹ️ Preserving vetting setting: {key}={existing.setting_value}")
                continue
            else:
                # Create new setting with production-aware default (first run only)
                new_setting = VettingConfig(
                    setting_key=key,
                    setting_value=default_value
                )
                db.session.add(new_setting)
                settings_created.append(key)
                logger.info(f"✅ Created vetting setting: {key}={default_value} (production={is_prod})")

        if settings_created:
            db.session.commit()
            logger.info(f"✅ Created vetting settings: {', '.join(settings_created)}")
        else:
            logger.info("✅ Vetting settings already up to date")

    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed vetting config: {str(e)}")
        raise


def seed_environment_monitoring(db):
    """
    Initialize environment monitoring settings for production

    Args:
        db: SQLAlchemy database instance
    """
    try:
        from models import EnvironmentStatus

        # Check if production environment monitoring exists
        prod_env = EnvironmentStatus.query.filter_by(
            environment_name='production'
        ).first()

        if not prod_env:
            # Create production environment monitoring
            prod_env = EnvironmentStatus(
                environment_name='production',
                environment_url='https://app.scoutgenius.ai',
                is_active=True,
                check_interval_minutes=5,
                alert_email='kroots@myticas.com',
                status='unknown',
                created_at=datetime.utcnow()
            )
            db.session.add(prod_env)
            db.session.commit()
            logger.info("✅ Created production environment monitoring configuration")
        else:
            logger.info("✅ Production environment monitoring already configured")

    except ImportError:
        logger.debug("ℹ️ EnvironmentStatus model not found - skipping environment monitoring seeding")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed environment monitoring: {str(e)}")


def seed_reference_refresh_log(db):
    """
    Initialize RefreshLog with baseline timestamp ONLY if table is empty
    NEVER overwrites existing refresh history - preserves real refresh data

    Args:
        db: SQLAlchemy database instance
    """
    try:
        from models import RefreshLog
        from datetime import date, timedelta

        # Use current time minus 120h so the scheduler will fire the first
        # refresh ~120h from deploy, not show a stale historical date.
        baseline_time = datetime.utcnow() - timedelta(hours=120)
        baseline_date = baseline_time.date()

        # Check if any refresh log entries exist
        existing_count = RefreshLog.query.count()

        if existing_count > 0:
            # Preserve existing refresh history - NEVER overwrite
            most_recent = RefreshLog.query.order_by(RefreshLog.refresh_time.desc()).first()
            logger.info(f"✅ RefreshLog has {existing_count} entries (most recent: {most_recent.refresh_time} UTC) - preserving history")
        else:
            # No entries exist - create baseline for new deployments only
            initial_log = RefreshLog(
                refresh_date=baseline_date,
                refresh_time=baseline_time,
                jobs_updated=65
            )
            db.session.add(initial_log)
            db.session.commit()
            logger.info(f"✅ Created baseline RefreshLog entry: {baseline_time} UTC (new deployment)")

    except ImportError:
        logger.debug("ℹ️ RefreshLog model not found - skipping refresh log seeding")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed refresh log: {str(e)}")


def seed_recruiter_mappings(db, RecruiterMapping):
    """
    Seed recruiter to LinkedIn tag mappings (idempotent)

    Args:
        db: SQLAlchemy database instance
        RecruiterMapping: RecruiterMapping model class
    """
    # Master list of recruiter name to LinkedIn tag mappings
    recruiter_mappings = [
        ('Adam Gebara', '#LI-AG1'),
        ('Amanda Messina', '#LI-AM1'),
        ('Amanda Messina (Smith)', '#LI-AM1'),
        ('Austin Zachrich', '#LI-AZ1'),
        ('Bryan Chinzorig', '#LI-BC1'),
        ('Chris Carter', '#LI-CC1'),
        ('Christine Carter', '#LI-CC1'),
        ('Dan Sifer', '#LI-DS1'),
        ('Daniel Sifer', '#LI-DS1'),
        ('Dawn Geistert-Dixon', '#LI-DG1'),
        ('Dominic Scaletta', '#LI-DS2'),
        ('Innocent Nangoma', '#LI-IN1'),
        ('Jayne Kritschgau', '#LI-JK1'),
        ('Julie Johnson', '#LI-JJ1'),
        ('Kaniz Abedin', '#LI-KA1'),
        ('Kyle Roots', '#LI-KR1'),
        ('Kellie Miller', '#LI-KM1'),
        ('Lisa Keirsted', '#LI-DS1'),
        ('Lisa Mattis-Keirsted', '#LI-LM1'),
        ('Maddie Lewis', '#LI-ML1'),
        ('Madhu Sinha', '#LI-MS1'),
        ('Matheo Theodossiou', '#LI-MT1'),
        ('Michael Billiu', '#LI-MB1'),
        ('Michael Theodossiou', '#LI-MT2'),
        ('Michelle Corino', '#LI-MC1'),
        ('Mike Gebara', '#LI-MG1'),
        ('Mike Scalzitti', '#LI-MS2'),
        ('Myticas Recruiter', '#LI-RS1'),
        ('Nick Theodossiou', '#LI-NT1'),
        ('Rachel Mann', '#LI-RM1'),
        ('Rachelle Fite', '#LI-RF1'),
        ('Reena Setya', '#LI-RS2'),
        ('Runa Parmar', '#LI-RP1'),
        ('Ryan Green', '#LI-RG1'),
        ('Ryan Oliver', '#LI-RO1'),
        ('Sarah Ferris', '#LI-SF1'),
        ('Sarah Ferris CSP', '#LI-SF1'),
        ('Shikha Gurung', '#LI-SG1'),
        ('Tarra Dziurman', '#LI-TD1'),
        ('Tray Prewitt', '#LI-TP1'),
    ]

    mappings_created = 0
    mappings_updated = 0

    for recruiter_name, linkedin_tag in recruiter_mappings:
        existing_mapping = RecruiterMapping.query.filter_by(recruiter_name=recruiter_name).first()

        if existing_mapping:
            # Update if tag changed
            if existing_mapping.linkedin_tag != linkedin_tag:
                existing_mapping.linkedin_tag = linkedin_tag
                mappings_updated += 1
        else:
            # Create new mapping
            new_mapping = RecruiterMapping(
                recruiter_name=recruiter_name,
                linkedin_tag=linkedin_tag
            )
            db.session.add(new_mapping)
            mappings_created += 1

    db.session.commit()

    if mappings_created > 0:
        logger.info(f"✅ Created {mappings_created} new recruiter mappings")
    if mappings_updated > 0:
        logger.info(f"🔄 Updated {mappings_updated} recruiter mappings")
    if mappings_created == 0 and mappings_updated == 0:
        logger.info(f"✅ All {len(recruiter_mappings)} recruiter mappings already up to date")


def seed_builtin_automations(db):
    # Late import: BUILTIN_AUTOMATIONS / STALE_BUILTIN_* live on seed_database
    # for backward compat with any external test that imports them directly.
    import seed_database

    try:
        from models import AutomationTask

        deleted = 0
        for task in AutomationTask.query.all():
            should_delete = False
            if task.name in seed_database.STALE_BUILTIN_NAMES:
                should_delete = True
            elif task.config_json:
                try:
                    cfg = json.loads(task.config_json)
                    if cfg.get("builtin_key") in seed_database.STALE_BUILTIN_KEYS:
                        should_delete = True
                except (json.JSONDecodeError, TypeError):
                    pass
            if should_delete:
                db.session.delete(task)
                deleted += 1
        if deleted > 0:
            db.session.commit()
            logger.info(f"🧹 Removed {deleted} stale automation record(s)")

        existing_keys = set()
        for task in AutomationTask.query.all():
            if task.config_json:
                try:
                    cfg = json.loads(task.config_json)
                    if cfg.get("builtin_key"):
                        existing_keys.add(cfg["builtin_key"])
                except (json.JSONDecodeError, TypeError):
                    pass

        created = 0
        for auto in seed_database.BUILTIN_AUTOMATIONS:
            if auto["builtin_key"] in existing_keys:
                continue
            task = AutomationTask(
                name=auto["name"],
                description=auto["description"],
                automation_type=auto["automation_type"],
                status="draft",
                config_json=json.dumps({"builtin_key": auto["builtin_key"]}),
            )
            db.session.add(task)
            created += 1

        if created > 0:
            db.session.commit()
            logger.info(f"✅ Seeded {created} built-in automations")
        else:
            logger.info(f"✅ All {len(seed_database.BUILTIN_AUTOMATIONS)} built-in automations already exist")
    except ImportError:
        logger.debug("ℹ️ AutomationTask model not found - skipping automation seeding")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed built-in automations: {str(e)}")
