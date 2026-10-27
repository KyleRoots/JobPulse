"""Fixtures for the PostgreSQL competing-connection tests (UNRUN artifact).

Run only as ``python3 -m pytest -p no:cacheprovider tests_pg`` against the
disposable cluster described in the setup proposal. Without a valid target
every test FAILS with "PostgreSQL integration tests NOT RUN"; nothing is
skipped and nothing falls back to SQLite. ``pytest.ini`` keeps this directory
out of the default ``tests`` run.
"""

import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests_pg import pg_guard  # noqa: E402

try:
    _TARGET = pg_guard.validate_target(os.environ)
    _REFUSAL = None
except pg_guard.PgTargetRefused as e:
    _TARGET = None
    _REFUSAL = str(e)

for _key in (
    'SENDGRID_API_KEY', 'SENDGRID_INBOUND_WEBHOOK_SECRET', 'TWILIO_ACCOUNT_SID',
    'TWILIO_AUTH_TOKEN', 'NEVERBOUNCE_API_KEY', 'OPENAI_API_KEY',
    'BULLHORN_CLIENT_ID', 'BULLHORN_CLIENT_SECRET', 'BULLHORN_USERNAME',
    'BULLHORN_PASSWORD', 'BH_CLIENT_ID', 'BH_CLIENT_SECRET', 'BH_USERNAME',
    'BH_PASSWORD', 'SFTP_HOSTNAME', 'SFTP_HOST', 'SFTP_USERNAME', 'SFTP_USER',
    'SFTP_PASSWORD', 'SFTP_KEY', 'SFTP_DIRECTORY',
):
    os.environ.pop(_key, None)

import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *a, **k: False

if _TARGET is not None:
    os.environ['DATABASE_URL'] = _TARGET
    os.environ['FLASK_ENV'] = 'testing'
    os.environ['TESTING'] = 'true'
    os.environ.setdefault('SESSION_SECRET', 'synthetic-pg-test-session-secret')


def pytest_configure(config):
    config.addinivalue_line('markers', 'pg_unrun: PostgreSQL test written offline and not yet executed')


@pytest.fixture(scope='session')
def app():
    if _TARGET is None:
        pytest.fail(f'PostgreSQL integration tests NOT RUN: {_REFUSAL}', pytrace=False)
    from app import app as flask_app, db
    from extensions import limiter

    flask_app.config.update({'TESTING': True, 'RATELIMIT_ENABLED': False})
    limiter.enabled = False
    with flask_app.app_context():
        with db.engine.connect() as conn:
            pg_guard.verify_live_target(conn)
        db.create_all()
        yield flask_app
        db.session.remove()


@pytest.fixture(autouse=True)
def _clean(app):
    from app import db
    from models import (
        CandidateJobMatch, CandidateVettingLog, EmbeddingFilterLog, EscalationLog,
        ParsedEmail, ScoutVettingSession, VettingAuditLog, VettingConfig,
    )

    def wipe():
        with app.app_context():
            ScoutVettingSession.query.delete()
            EmbeddingFilterLog.query.delete()
            EscalationLog.query.delete()
            CandidateJobMatch.query.delete()
            CandidateVettingLog.query.delete()
            VettingAuditLog.query.delete()
            ParsedEmail.query.delete()
            VettingConfig.query.filter_by(setting_key='vetting_cutoff_date').delete()
            db.session.commit()

    wipe()
    yield
    wipe()


@pytest.fixture
def harness(app):
    from tests.test_revet_parsed_email_fanout import Harness
    return Harness(app)


class Worker:
    """Runs ``fn`` on its own thread, app context, session and connection."""

    def __init__(self, app, fn, *args, _name=None, **kwargs):
        self.result = None
        self.error = None
        self.pid = None
        self._pid_ready = threading.Event()

        def run():
            from sqlalchemy import text
            from app import db
            with app.app_context():
                try:
                    self.pid = db.session.execute(text('SELECT pg_backend_pid()')).scalar()
                    db.session.commit()
                    self._pid_ready.set()
                    self.result = fn(*args, **kwargs)
                except BaseException as e:  # noqa: BLE001
                    self.error = e
                finally:
                    self._pid_ready.set()
                    db.session.remove()

        self.thread = threading.Thread(target=run, name=_name, daemon=True)
        self.thread.start()
        assert self._pid_ready.wait(10)

    def join(self, timeout=15):
        self.thread.join(timeout)
        assert not self.thread.is_alive(), 'worker did not finish'
        return self


def wait_until_lock_wait(app, pid, timeout=10.0):
    """Block until backend ``pid`` is waiting on a heavyweight lock. Polls
    pg_stat_activity from its own connection; returns the wait event."""
    from sqlalchemy import text
    from app import db

    deadline = time.monotonic() + timeout
    with db.engine.connect() as conn:
        while time.monotonic() < deadline:
            row = conn.execute(text(
                "SELECT wait_event_type, wait_event FROM pg_stat_activity WHERE pid = :pid"
            ), {'pid': pid}).first()
            conn.rollback()
            if row is not None and row[0] == 'Lock':
                return row[1]
            time.sleep(0.01)
    raise AssertionError(f'backend {pid} never waited on a lock')
