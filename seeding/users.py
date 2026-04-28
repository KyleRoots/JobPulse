"""User account, support contact, and module subscription seeders.

The Myticas / STSI support contact lists themselves live in `seed_database`
as module constants (SUPPORT_CONTACTS_MYTICAS / SUPPORT_CONTACTS_STSI) so
existing callers like `from seed_database import SUPPORT_CONTACTS_STSI`
continue to work. The seeders here read those constants via the
seed_database module reference at call time.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def create_admin_user(db, User):
    """
    Create admin user if it doesn't exist, or update if credentials changed (idempotent with rotation)

    Args:
        db: SQLAlchemy database instance
        User: User model class

    Returns:
        tuple: (user, created) - User object and boolean indicating if created
    """
    # Late import so test patches on `seed_database.get_admin_config` and
    # `seed_database.is_production_environment` are observed at call time.
    import seed_database

    config = seed_database.get_admin_config()

    # First, try to find existing admin user by is_admin=True (independent of current env values)
    # This ensures we update the same admin even if username/email change
    existing_user = User.query.filter_by(is_admin=True).first()

    # If no admin found by is_admin, try to find by current env values (for backwards compatibility)
    if not existing_user:
        existing_user = User.query.filter(
            (User.username == config['username']) | (User.email == config['email'])
        ).first()

    if existing_user:
        logger.info(f"✅ Admin user already exists: {existing_user.username} ({existing_user.email})")

        # Track what was updated for logging
        updates = []

        # Update admin status if needed
        if not existing_user.is_admin:
            existing_user.is_admin = True
            updates.append("admin status")

        # Update username if changed
        if existing_user.username != config['username']:
            old_username = existing_user.username
            existing_user.username = config['username']
            updates.append(f"username ({old_username} -> {config['username']})")

        # Update email if changed
        if existing_user.email != config['email']:
            old_email = existing_user.email
            existing_user.email = config['email']
            updates.append(f"email ({old_email} -> {config['email']})")

        # Always update password from current environment (enables rotation)
        # Note: We can't check if password changed (it's hashed), so always update
        existing_user.set_password(config['password'])
        updates.append("password (rotated from env)")

        if updates:
            db.session.commit()
            logger.info(f"🔄 Updated admin user: {', '.join(updates)}")
        else:
            logger.info(f"✅ Admin user up to date (no changes needed)")

        return existing_user, False

    # Create new admin user
    try:
        admin_user = User(
            username=config['username'],
            email=config['email'],
            is_admin=True,
            company='Myticas Consulting',
            created_at=datetime.utcnow()
        )
        admin_user.set_password(config['password'])

        db.session.add(admin_user)
        db.session.commit()

        env_type = "production" if seed_database.is_production_environment() else "development"
        logger.info(f"✅ Created new admin user in {env_type}: {admin_user.username} ({admin_user.email})")

        return admin_user, True

    except Exception as e:
        db.session.rollback()
        logger.error(f"❌ Failed to create admin user: {str(e)}")
        raise


def _seed_brand_contacts(db, brand, contact_list):
    from models import SupportContact
    existing_by_email = {c.email: c for c in SupportContact.query.filter_by(brand=brand).all()}
    added = 0
    updated = 0
    removed = 0
    canonical_emails = {c['email'] for c in contact_list}
    for email, contact in existing_by_email.items():
        if email not in canonical_emails:
            db.session.delete(contact)
            removed += 1
            logger.info(f"🗑️ Removed stale {brand} support contact: {contact.first_name} {contact.last_name} ({email})")
    for contact_data in contact_list:
        if contact_data['email'] not in existing_by_email:
            contact = SupportContact(
                first_name=contact_data['first_name'],
                last_name=contact_data['last_name'],
                email=contact_data['email'],
                brand=brand,
                department=contact_data.get('department')
            )
            db.session.add(contact)
            added += 1
        else:
            existing = existing_by_email[contact_data['email']]
            if contact_data.get('department') and existing.department != contact_data['department']:
                existing.department = contact_data['department']
                updated += 1
    if added > 0 or updated > 0 or removed > 0:
        db.session.commit()
        logger.info(f"✅ {brand} support contacts: {added} added, {updated} updated, {removed} removed")
    else:
        logger.info(f"✅ All {len(contact_list)} {brand} support contacts already up to date")


def seed_support_contacts(db):
    # Late import: SUPPORT_CONTACTS_* lists live on seed_database for backward compat.
    import seed_database

    try:
        _seed_brand_contacts(db, 'Myticas', seed_database.SUPPORT_CONTACTS_MYTICAS)
        _seed_brand_contacts(db, 'STSI', seed_database.SUPPORT_CONTACTS_STSI)
    except ImportError:
        logger.debug("ℹ️ SupportContact model not found - skipping support contact seeding")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed support contacts: {str(e)}")


def ensure_module_subscriptions(db, User):
    """
    Ensure all non-admin users have scout_support and scout_screening in their
    module subscriptions. One-time upgrade — idempotent, only adds missing modules.
    """
    try:
        import json
        updated = 0
        users = User.query.filter_by(is_admin=False).all()
        for user in users:
            current = user.get_modules()
            if not current:
                continue
            needed = set()
            if 'scout_screening' not in current:
                needed.add('scout_screening')
            if 'scout_support' not in current:
                needed.add('scout_support')
            if needed:
                user.set_modules(current + list(needed))
                updated += 1
        if updated > 0:
            db.session.commit()
            logger.info(f"✅ Module subscriptions: added missing modules to {updated} users")
        else:
            logger.info(f"✅ All users already have correct module subscriptions")
    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to update module subscriptions: {str(e)}")


def seed_myticas_users(db, User):
    """
    Create locked login accounts for all Myticas support contacts.

    Idempotent — skips contacts whose email already has a User account
    (notably kroots@myticas.com which is already attached to the `admin`
    user). All accounts are subscribed to scout_inbound, scout_screening,
    scout_support and locked until a welcome email / password reset is
    sent by the admin.

    Restores the 24 Myticas user logins that were lost when the production
    database was reseeded from the dev defaults on 2026-04-28. Forward-only:
    historical login activity / passwords are NOT recovered, but the
    accounts exist again so the admin can issue resets.
    """
    # Late import: SUPPORT_CONTACTS_MYTICAS lives on seed_database for backward compat.
    import seed_database

    try:
        existing_emails = {u.email for u in User.query.all()}
        existing_usernames = {u.username for u in User.query.all()}

        created = 0
        for c in seed_database.SUPPORT_CONTACTS_MYTICAS:
            if c['email'] in existing_emails:
                continue

            first = c['first_name'][0].lower()
            last = c['last_name'].lower().replace(' ', '').replace('-', '').replace("'", '')
            base_username = f'{first}{last}.myt'
            username = base_username
            suffix = 2
            while username in existing_usernames:
                username = f'{base_username}{suffix}'
                suffix += 1

            full_name = f"{c['first_name']} {c['last_name']}"
            user = User(
                username=username,
                email=c['email'],
                display_name=full_name,
                company='Myticas Consulting',
                is_admin=False,
                is_company_admin=False,
                password_hash='!locked',
                created_at=datetime.utcnow(),
            )
            user.set_modules(['scout_inbound', 'scout_screening', 'scout_support'])
            db.session.add(user)
            existing_usernames.add(username)
            existing_emails.add(c['email'])
            created += 1

        if created > 0:
            db.session.commit()
            logger.info(f"✅ Myticas users: created {created} locked login accounts")
        else:
            logger.info(f"✅ All Myticas user accounts already exist")

        # Verification: every SUPPORT_CONTACTS_MYTICAS entry should now map to a User row.
        # Surface a loud warning if the actual count is short, so a partial-restoration
        # failure doesn't pass silently.
        expected_emails = {c['email'] for c in seed_database.SUPPORT_CONTACTS_MYTICAS}
        actual_emails = {u.email for u in User.query.filter(
            User.email.in_(list(expected_emails))).all()}
        missing = expected_emails - actual_emails
        if missing:
            logger.warning(
                f"⚠️ Myticas user restoration INCOMPLETE: "
                f"{len(actual_emails)}/{len(expected_emails)} accounts present. "
                f"Missing: {sorted(missing)}"
            )
        else:
            logger.info(
                f"✅ Myticas user restoration verified: all "
                f"{len(expected_emails)} contacts have an account "
                f"(by email)"
            )

    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed Myticas users: {str(e)}")


def seed_stsi_users(db, User):
    """
    Create locked login accounts for all STSI support contacts.
    Idempotent — skips contacts whose email already has a User account.
    All accounts are subscribed to scout_inbound, scout_screening, scout_support
    and locked until a welcome email is sent by the admin.
    """
    # Late import: SUPPORT_CONTACTS_STSI lives on seed_database for backward compat.
    import seed_database

    try:
        existing_emails = {u.email for u in User.query.all()}
        existing_usernames = {u.username for u in User.query.all()}

        created = 0
        for c in seed_database.SUPPORT_CONTACTS_STSI:
            if c['email'] in existing_emails:
                continue

            first = c['first_name'][0].lower()
            last = c['last_name'].lower().replace(' ', '').replace('-', '')
            base_username = f'{first}{last}.stsi'
            username = base_username
            suffix = 2
            while username in existing_usernames:
                username = f'{base_username}{suffix}'
                suffix += 1

            full_name = f"{c['first_name']} {c['last_name']}"
            user = User(
                username=username,
                email=c['email'],
                display_name=full_name,
                company='STSI Group',
                is_admin=False,
                is_company_admin=False,
                password_hash='!locked',
                created_at=datetime.utcnow(),
            )
            user.set_modules(['scout_inbound', 'scout_screening', 'scout_support'])
            db.session.add(user)
            existing_usernames.add(username)
            existing_emails.add(c['email'])
            created += 1

        if created > 0:
            db.session.commit()
            logger.info(f"✅ STSI users: created {created} locked login accounts")
        else:
            logger.info(f"✅ All STSI user accounts already exist")

    except Exception as e:
        db.session.rollback()
        logger.warning(f"⚠️ Failed to seed STSI users: {str(e)}")
