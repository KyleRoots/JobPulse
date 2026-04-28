"""Database seeding package.

Submodules:
    - guards: production-database safety guard for fresh deployments
    - config: environment-driven config providers (admin/SFTP/Bullhorn)
    - users: User account, support contact, and module subscription seeders
    - settings: GlobalSettings/VettingConfig/BullhornMonitor/etc. seeders
    - migrations: schema migrations, data migrations, and audit utilities

All public functions are re-exported by the top-level `seed_database` module
for backward compatibility with existing callers and tests. Submodules call
shared helpers (is_production_environment, _is_test_environment, the SUPPORT_*
contact lists, BUILTIN_AUTOMATIONS, etc.) via late module-reference lookup
on `seed_database` so test patches like `patch('seed_database.X')` continue
to apply at runtime.
"""
