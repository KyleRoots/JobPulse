"""Strict disposable-target guard for the PostgreSQL integration tests.

The tests in this directory drop and rewrite tables, so they run only against
a throwaway loopback cluster that was created for them. Every rule below must
hold, before connecting (``validate_target``) and after (``verify_live_target``);
anything else refuses the whole session. There is no SQLite fallback.
"""

import re
from urllib.parse import urlsplit, unquote

DSN_ENV = 'REVET_PG_TEST_DSN'
ACK_ENV = 'REVET_PG_TEST_ACK'
ACK_VALUE = 'disposable-loopback-cluster-55432'

PORT = 55432
USER = 'revet_test'
LOOPBACK_HOSTS = ('127.0.0.1', '::1', 'localhost')
DATABASE_RE = re.compile(r'^revet_pg_test_[a-z0-9_]{1,40}$')
SERVER_MAJOR = 16
MARKER_TABLE = 'revet_disposable_marker'

# Presence of any of these means the shell is attached to a real deployment.
PRODUCTION_ENV_MARKERS = (
    'DATABASE_URL', 'DATABASE_PUBLIC_URL', 'PGHOST', 'PGDATABASE', 'PGUSER',
    'PGSERVICE', 'PGSERVICEFILE', 'PGPASSFILE',
    'RAILWAY_ENVIRONMENT', 'RAILWAY_ENVIRONMENT_NAME', 'RAILWAY_PROJECT_ID',
    'RAILWAY_SERVICE_ID', 'RAILWAY_TOKEN',
)


class PgTargetRefused(RuntimeError):
    pass


def validate_target(env) -> str:
    """Return a SQLAlchemy URL for the disposable target, or raise."""
    dsn = env.get(DSN_ENV)
    if not dsn:
        raise PgTargetRefused(f'{DSN_ENV} is not set')
    if env.get(ACK_ENV) != ACK_VALUE:
        raise PgTargetRefused(f'{ACK_ENV} must equal {ACK_VALUE!r}')
    present = sorted(k for k in PRODUCTION_ENV_MARKERS if env.get(k))
    if present:
        raise PgTargetRefused(f'refusing with deployment variables set: {present}')

    parts = urlsplit(dsn)
    if parts.scheme not in ('postgresql', 'postgresql+psycopg2'):
        raise PgTargetRefused(f'unsupported scheme {parts.scheme!r}')
    if parts.query or parts.fragment:
        raise PgTargetRefused('query parameters are not allowed (host/service overrides)')
    if ',' in parts.netloc:
        raise PgTargetRefused('multiple hosts are not allowed')
    if parts.hostname not in LOOPBACK_HOSTS:
        raise PgTargetRefused(f'host {parts.hostname!r} is not loopback')
    if parts.port != PORT:
        raise PgTargetRefused(f'port {parts.port!r} is not {PORT}')
    if unquote(parts.username or '') != USER:
        raise PgTargetRefused(f'user must be {USER!r}')
    if not parts.password:
        raise PgTargetRefused('a password is required (scram-sha-256 on loopback)')
    database = parts.path.lstrip('/')
    if not DATABASE_RE.match(database):
        raise PgTargetRefused(f'database {database!r} does not match {DATABASE_RE.pattern}')
    return 'postgresql+psycopg2://' + dsn.split('://', 1)[1]


def verify_live_target(conn) -> dict:
    """Check the connected server is the disposable one. ``conn`` is a
    SQLAlchemy connection."""
    from sqlalchemy import text

    row = conn.execute(text(
        "SELECT current_database() AS db, current_user AS usr, "
        "host(inet_server_addr()) AS addr, inet_server_port() AS port, "
        "current_setting('server_version_num')::int AS ver"
    )).mappings().one()
    if row['addr'] not in ('127.0.0.1', '::1'):
        raise PgTargetRefused(f"server address {row['addr']!r} is not loopback")
    if row['port'] != PORT:
        raise PgTargetRefused(f"server port {row['port']} is not {PORT}")
    if row['usr'] != USER or not DATABASE_RE.match(row['db']):
        raise PgTargetRefused(f"connected as {row['usr']!r} to {row['db']!r}")
    if row['ver'] // 10000 != SERVER_MAJOR:
        raise PgTargetRefused(f"server_version_num {row['ver']} is not PostgreSQL {SERVER_MAJOR}")
    token = conn.execute(text(
        f"SELECT token FROM {MARKER_TABLE}"
    )).scalars().all()
    if token != [row['db']]:
        raise PgTargetRefused(f'{MARKER_TABLE} must hold exactly one row equal to the database name')
    return dict(row)
