"""Schema migrations, data migrations, and audit utilities.

These run inside seed_database() at boot time:
  - run_schema_migrations: ALTER TABLE / CREATE INDEX work that
    db.create_all() doesn't handle.
  - migrate_legacy_custom_requirements: one-time data migration from
    custom_requirements -> edited_requirements.
  - log_critical_settings_state: post-seed audit snapshot in logs.
"""

import os
import re
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def run_schema_migrations(db):
    """
    Run any necessary schema migrations that db.create_all() won't handle.
    Adds missing columns to existing tables.
    """
    from sqlalchemy import text

    migrations = [
        # Add location columns to job_vetting_requirements (added Jan 2026)
        ("job_vetting_requirements", "job_location", "VARCHAR(255)"),
        ("job_vetting_requirements", "job_work_type", "VARCHAR(50)"),
        # Add years_analysis_json to candidate_job_match for auditability (added Feb 2026)
        ("candidate_job_match", "years_analysis_json", "TEXT"),
        # Add employer prestige boost toggle (added Apr 2026)
        ("job_vetting_requirements", "employer_prestige_boost", "BOOLEAN DEFAULT FALSE"),
        # Add employer prestige tracking to candidate_job_match (added Apr 2026)
        ("candidate_job_match", "prestige_employer", "VARCHAR(255)"),
        ("candidate_job_match", "prestige_boost_applied", "BOOLEAN DEFAULT FALSE"),
        # Add inline-editable requirements + provenance metadata (added Apr 2026)
        ("job_vetting_requirements", "edited_requirements", "TEXT"),
        ("job_vetting_requirements", "requirements_edited_at", "TIMESTAMP"),
        ("job_vetting_requirements", "requirements_edited_by", "VARCHAR(255)"),
        # Scout Vetting stagger + mid-session flag (added Apr 2026)
        ("scout_vetting_session", "scheduled_outreach_at", "TIMESTAMP"),
        ("scout_vetting_session", "requirements_changed_mid_session", "BOOLEAN"),
    ]

    _SAFE_IDENTIFIER = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')
    _SAFE_COL_TYPE = re.compile(r'^[A-Z()0-9 ]+$')

    for table, column, col_type in migrations:
        try:
            if not (_SAFE_IDENTIFIER.match(table) and _SAFE_IDENTIFIER.match(column) and _SAFE_COL_TYPE.match(col_type)):
                logger.warning(f"⚠️ Migration skipped: unsafe identifier in {table}.{column} {col_type}")
                continue
            check_sql = text("""
                SELECT column_name FROM information_schema.columns 
                WHERE table_name = :table AND column_name = :column
            """)
            result = db.session.execute(check_sql, {'table': table, 'column': column})
            if result.fetchone() is None:
                alter_sql = text('ALTER TABLE ' + table + ' ADD COLUMN ' + column + ' ' + col_type)
                db.session.execute(alter_sql)
                db.session.commit()
                logger.info(f"✅ Added column {column} to {table}")
        except Exception as e:
            db.session.rollback()
            logger.warning(f"⚠️ Migration skipped for {table}.{column}: {str(e)}")

    # Add is_company_admin to user table (added Feb 2026)
    # Handled separately because 'user' is a PostgreSQL reserved word requiring quoting
    try:
        check_sql = text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'user' AND column_name = 'is_company_admin'
        """)
        result = db.session.execute(check_sql)
        if result.fetchone() is None:
            db.session.execute(text('ALTER TABLE "user" ADD COLUMN is_company_admin BOOLEAN DEFAULT FALSE'))
            db.session.commit()
            logger.info("✅ Added column is_company_admin to user table")
        else:
            logger.info("ℹ️ Column is_company_admin already exists on user table")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for user.is_company_admin: {str(e)}")

    # Add last_active_at to user table (added March 2026 for Active Users tracking)
    try:
        result = db.session.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'user' AND column_name = 'last_active_at'
        """))
        if result.fetchone() is None:
            db.session.execute(text('ALTER TABLE "user" ADD COLUMN last_active_at TIMESTAMP'))
            db.session.commit()
            logger.info("✅ Added column last_active_at to user table")
        else:
            logger.info("ℹ️ Column last_active_at already exists on user table")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for user.last_active_at: {str(e)}")

    # Add is_sandbox to candidate_vetting_log (added Feb 2026 for Vetting Sandbox feature)
    try:
        result = db.session.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'candidate_vetting_log' AND column_name = 'is_sandbox'
        """))
        if result.fetchone() is None:
            db.session.execute(text(
                'ALTER TABLE candidate_vetting_log ADD COLUMN is_sandbox BOOLEAN NOT NULL DEFAULT FALSE'
            ))
            db.session.commit()
            logger.info("✅ Added column is_sandbox to candidate_vetting_log table")
        else:
            logger.info("ℹ️ Column is_sandbox already exists on candidate_vetting_log table")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for candidate_vetting_log.is_sandbox: {str(e)}")

    # Add is_sandbox to scout_vetting_session (added Feb 2026 for Vetting Sandbox feature)
    try:
        result = db.session.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'scout_vetting_session' AND column_name = 'is_sandbox'
        """))
        if result.fetchone() is None:
            db.session.execute(text(
                'ALTER TABLE scout_vetting_session ADD COLUMN is_sandbox BOOLEAN NOT NULL DEFAULT FALSE'
            ))
            db.session.commit()
            logger.info("✅ Added column is_sandbox to scout_vetting_session table")
        else:
            logger.info("ℹ️ Column is_sandbox already exists on scout_vetting_session table")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for scout_vetting_session.is_sandbox: {str(e)}")

    # Add thread_message_id to scout_vetting_session (added Apr 2026 for email thread history)
    try:
        result = db.session.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'scout_vetting_session' AND column_name = 'thread_message_id'
        """))
        if result.fetchone() is None:
            db.session.execute(text(
                'ALTER TABLE scout_vetting_session ADD COLUMN thread_message_id VARCHAR(255)'
            ))
            db.session.commit()
            logger.info("✅ Added column thread_message_id to scout_vetting_session table")
        else:
            logger.info("ℹ️ Column thread_message_id already exists on scout_vetting_session table")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for scout_vetting_session.thread_message_id: {str(e)}")

    # Add resolution_note, resolved_by, and Send-to-Build workflow columns to support_ticket
    for col_name, col_type in [
        ('resolution_note', 'TEXT'),
        ('resolved_by', 'VARCHAR(255)'),
        ('sent_to_build_at', 'TIMESTAMP'),
        ('sent_to_build_by', 'VARCHAR(255)'),
        ('build_summary', 'TEXT'),
        ('deployed_at', 'TIMESTAMP'),
        ('deployed_by', 'VARCHAR(255)'),
        ('deploy_commit_link', 'VARCHAR(500)'),
    ]:
        try:
            check = text(f"SELECT column_name FROM information_schema.columns WHERE table_name='support_ticket' AND column_name='{col_name}'")
            exists = db.session.execute(check).fetchone()
            if not exists:
                db.session.execute(text(f"ALTER TABLE support_ticket ADD COLUMN {col_name} {col_type}"))
                db.session.commit()
                logger.info(f"✅ Added column {col_name} to support_ticket table")
            else:
                logger.info(f"ℹ️ Column {col_name} already exists on support_ticket table")
        except Exception as e:
            db.session.rollback()
            logger.warning(f"⚠️ Migration skipped for support_ticket.{col_name}: {str(e)}")

    for col_name, col_type in [('onedrive_item_id', 'VARCHAR(255)'), ('onedrive_etag', 'VARCHAR(255)'), ('onedrive_folder_id', 'VARCHAR(255)')]:
        try:
            check = text(f"SELECT column_name FROM information_schema.columns WHERE table_name='knowledge_document' AND column_name='{col_name}'")
            exists = db.session.execute(check).fetchone()
            if not exists:
                db.session.execute(text(f"ALTER TABLE knowledge_document ADD COLUMN {col_name} {col_type}"))
                db.session.commit()
                logger.info(f"✅ Added column {col_name} to knowledge_document table")
            else:
                logger.info(f"ℹ️ Column {col_name} already exists on knowledge_document table")
        except Exception as e:
            db.session.rollback()
            logger.warning(f"⚠️ Migration skipped for knowledge_document.{col_name}: {str(e)}")

    # Add vetting_retry_count to parsed_email (Apr 2026 - tracks 0% failure retries to prevent endless recycling)
    try:
        result = db.session.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'parsed_email' AND column_name = 'vetting_retry_count'
        """))
        if result.fetchone() is None:
            db.session.execute(text(
                "ALTER TABLE parsed_email ADD COLUMN vetting_retry_count INTEGER NOT NULL DEFAULT 0"
            ))
            db.session.commit()
            logger.info("✅ Added column vetting_retry_count to parsed_email table")
        else:
            logger.info("ℹ️ Column vetting_retry_count already exists on parsed_email table")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for parsed_email.vetting_retry_count: {str(e)}")

    # Add conversation_id to support_attachment (Apr 2026 - links attachments to specific conversation entries)
    try:
        result = db.session.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'support_attachment' AND column_name = 'conversation_id'
        """))
        if result.fetchone() is None:
            db.session.execute(text(
                "ALTER TABLE support_attachment ADD COLUMN conversation_id INTEGER REFERENCES support_conversation(id)"
            ))
            db.session.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_support_attachment_conversation_id ON support_attachment(conversation_id)"
            ))
            db.session.commit()
            logger.info("✅ Added column conversation_id to support_attachment table")
        else:
            logger.info("ℹ️ Column conversation_id already exists on support_attachment table")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for support_attachment.conversation_id: {str(e)}")

    # Drop UNIQUE constraint on candidate_vetting_log.bullhorn_candidate_id
    # (Feb 2026 - allow multiple vetting logs per candidate for re-applications)
    try:
        # Check if the unique constraint still exists (PostgreSQL only)
        check_sql = text("""
            SELECT constraint_name FROM information_schema.table_constraints
            WHERE table_name = 'candidate_vetting_log'
              AND constraint_type = 'UNIQUE'
              AND constraint_name LIKE '%bullhorn_candidate_id%'
        """)
        result = db.session.execute(check_sql)
        constraint = result.fetchone()
        if constraint:
            constraint_name = constraint[0]
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', constraint_name):
                logger.warning(f"⚠️ Unsafe constraint name: {constraint_name}")
            else:
                drop_sql = text('ALTER TABLE candidate_vetting_log DROP CONSTRAINT ' + constraint_name)
                db.session.execute(drop_sql)
            db.session.commit()
            logger.info(f"✅ Dropped UNIQUE constraint {constraint_name} on candidate_vetting_log.bullhorn_candidate_id")
        else:
            logger.info("ℹ️ UNIQUE constraint on candidate_vetting_log.bullhorn_candidate_id already removed")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Migration skipped for dropping unique constraint: {str(e)}")

    # Ensure pg_trgm extension and trigram GIN index on candidate_vetting_log.candidate_name
    # (March 2026 — for server-side candidate search on Scout Screening portal)
    # Managed here instead of Alembic because Replit's deployment auto-migration
    # generates incorrect SQL for GIN indexes (omits gin_trgm_ops operator class).
    # Index creation is guarded by REPLIT_DEPLOYMENT so it only runs in the deployed
    # production environment — not in the dev workspace — preventing Replit's schema
    # diff from detecting the index and generating a broken migration on next deploy.
    try:
        db.session.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        db.session.commit()
        logger.info("ℹ️ pg_trgm extension ensured")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ pg_trgm extension creation skipped: {str(e)}")

    if os.environ.get('REPLIT_DEPLOYMENT'):
        try:
            result = db.session.execute(text("""
                SELECT indexname FROM pg_indexes
                WHERE tablename = 'candidate_vetting_log'
                  AND indexname = 'idx_vetting_log_candidate_name_trgm'
            """))
            if result.fetchone() is None:
                db.session.execute(text(
                    "CREATE INDEX idx_vetting_log_candidate_name_trgm "
                    "ON candidate_vetting_log USING gin (candidate_name gin_trgm_ops)"
                ))
                db.session.commit()
                logger.info("✅ Created trigram GIN index on candidate_vetting_log.candidate_name")
            else:
                logger.info("ℹ️ Trigram GIN index on candidate_vetting_log.candidate_name already exists")
        except Exception as e:
            db.session.rollback()
            logger.warning(f"⚠️ Trigram index creation skipped: {str(e)}")
    else:
        logger.info("ℹ️ Trigram GIN index creation skipped in dev workspace (runs on deployed prod only)")

    try:
        result = db.session.execute(text("""
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'candidate_job_match'
              AND indexname = 'idx_match_job_created'
        """))
        if result.fetchone() is None:
            db.session.execute(text(
                "CREATE INDEX idx_match_job_created "
                "ON candidate_job_match (bullhorn_job_id, created_at DESC)"
            ))
            db.session.commit()
            logger.info("✅ Created composite index idx_match_job_created on candidate_job_match")
        else:
            logger.info("ℹ️ Composite index idx_match_job_created already exists")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Composite index idx_match_job_created creation skipped: {str(e)}")


    prospector_tables = ['prospector_profile', 'prospector_run', 'prospect']
    for table_name in prospector_tables:
        try:
            result = db.session.execute(text("""
                SELECT table_name FROM information_schema.tables
                WHERE table_name = :table_name AND table_schema = 'public'
            """), {'table_name': table_name})
            if result.fetchone():
                logger.info(f"ℹ️ Prospector table '{table_name}' exists")
            else:
                logger.warning(f"⚠️ Prospector table '{table_name}' missing — db.create_all() should create it")
        except Exception as e:
            logger.warning(f"⚠️ Prospector table check failed for '{table_name}': {str(e)}")


def log_critical_settings_state(db):
    """
    Log the current DB values of all admin-controlled settings at startup.

    This runs at the END of seed_database() so every deploy produces a single,
    easy-to-audit snapshot proving no setting was silently changed.
    """
    from models import VettingConfig, GlobalSettings

    try:
        vetting_keys = [
            'vetting_enabled', 'send_recruiter_emails',
            'embedding_filter_enabled', 'embedding_similarity_threshold',
            'vetting_cutoff_date', 'match_threshold', 'batch_size',
            'layer2_model',
        ]
        global_keys = [
            'sftp_enabled', 'automated_uploads_enabled',
        ]

        parts = []
        for key in vetting_keys:
            s = VettingConfig.query.filter_by(setting_key=key).first()
            parts.append(f"{key}={s.setting_value if s else 'MISSING'}")
        for key in global_keys:
            s = GlobalSettings.query.filter_by(setting_key=key).first()
            parts.append(f"{key}={s.setting_value if s else 'MISSING'}")

        logger.info(f"🔒 STARTUP AUDIT — critical settings: {' | '.join(parts)}")
    except Exception as e:
        logger.warning(f"⚠️ Startup audit logging failed: {str(e)}")


def migrate_legacy_custom_requirements(db):
    """One-time data migration: convert legacy `custom_requirements` rows to the
    new `edited_requirements` field (Apr 2026 — inline editable requirements).

    For rows where both `ai_interpreted_requirements` and `custom_requirements`
    were populated, the AI list is preserved and the recruiter additions are
    appended in a clearly-marked block so the screening engine continues to see
    both. For rows with only `custom_requirements`, that text becomes the
    edited list directly. Idempotent — only touches rows where
    `edited_requirements` is still NULL.
    """
    from sqlalchemy import text

    try:
        result = db.session.execute(text(
            """
            SELECT id, ai_interpreted_requirements, custom_requirements, updated_at
            FROM job_vetting_requirements
            WHERE edited_requirements IS NULL
              AND custom_requirements IS NOT NULL
              AND TRIM(custom_requirements) <> ''
            """
        ))
        rows = result.fetchall()
        migrated = 0
        for row in rows:
            row_id = row[0]
            ai_text = (row[1] or '').strip()
            custom_text = (row[2] or '').strip()
            updated_at = row[3]

            if not custom_text:
                continue

            if ai_text:
                edited_text = (
                    f"{ai_text}\n\n"
                    f"--- RECRUITER NOTES (migrated from legacy override) ---\n"
                    f"{custom_text}"
                )
            else:
                edited_text = custom_text

            from datetime import datetime as _dt
            db.session.execute(
                text(
                    """
                    UPDATE job_vetting_requirements
                    SET edited_requirements = :edited,
                        requirements_edited_at = COALESCE(:edited_at, :fallback_now),
                        requirements_edited_by = 'migrated'
                    WHERE id = :row_id
                    """
                ),
                {'edited': edited_text, 'edited_at': updated_at,
                 'row_id': row_id, 'fallback_now': _dt.utcnow()}
            )
            migrated += 1

        if migrated:
            db.session.commit()
            logger.info(
                f"✅ Migrated {migrated} legacy custom_requirements row(s) to edited_requirements"
            )
        else:
            logger.info("ℹ️ No legacy custom_requirements rows needed migration")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Legacy custom_requirements migration skipped: {str(e)}")


def migrate_recruiter_lookback_to_24h(db):
    """
    One-time migration: upgrade recruiter_activity_lookback_minutes from the
    old 60-minute default to 1440 (24 hours).

    Only fires if the value is still exactly '60' — administrators who have
    already customised it above 60 are left untouched.
    Added April 2026 alongside the job-submission gate.
    """
    try:
        from models import VettingConfig
        row = VettingConfig.query.filter_by(
            setting_key='recruiter_activity_lookback_minutes'
        ).first()
        if row and row.setting_value == '60':
            row.setting_value = '1440'
            db.session.commit()
            logger.info(
                "✅ Migrated recruiter_activity_lookback_minutes: 60 → 1440 (24 hours)"
            )
        else:
            logger.info(
                "ℹ️ recruiter_activity_lookback_minutes already updated or not set to legacy default"
            )
    except Exception as e:
        db.session.rollback()
        logger.warning(
            f"⚠️ recruiter_activity_lookback_minutes migration skipped: {str(e)}"
        )
