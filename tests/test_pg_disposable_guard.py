"""Offline checks of the PostgreSQL test target guard (no connection made)."""

from unittest.mock import MagicMock

import pytest

from tests_pg.pg_guard import ACK_VALUE, PgTargetRefused, validate_target, verify_live_target

GOOD = 'postgresql://revet_test:synthetic-pass@127.0.0.1:55432/revet_pg_test_ci'


def _env(**overrides):
    env = {'REVET_PG_TEST_DSN': GOOD, 'REVET_PG_TEST_ACK': ACK_VALUE}
    env.update(overrides)
    return {k: v for k, v in env.items() if v is not None}


def test_accepts_only_the_disposable_loopback_target():
    assert validate_target(_env()) == 'postgresql+psycopg2://revet_test:synthetic-pass@127.0.0.1:55432/revet_pg_test_ci'


@pytest.mark.parametrize('overrides', [
    {'REVET_PG_TEST_DSN': None},
    {'REVET_PG_TEST_ACK': None},
    {'REVET_PG_TEST_ACK': 'yes'},
    {'DATABASE_URL': 'postgresql://u:p@db.internal:5432/app'},
    {'RAILWAY_ENVIRONMENT': 'production'},
    {'PGHOST': '10.0.0.5'},
    {'REVET_PG_TEST_DSN': 'sqlite:///revet_pg_test_ci.db'},
    {'REVET_PG_TEST_DSN': 'postgresql://revet_test:p@db.example.com:55432/revet_pg_test_ci'},
    {'REVET_PG_TEST_DSN': 'postgresql://revet_test:p@127.0.0.1:5432/revet_pg_test_ci'},
    {'REVET_PG_TEST_DSN': 'postgresql://postgres:p@127.0.0.1:55432/revet_pg_test_ci'},
    {'REVET_PG_TEST_DSN': 'postgresql://revet_test@127.0.0.1:55432/revet_pg_test_ci'},
    {'REVET_PG_TEST_DSN': 'postgresql://revet_test:p@127.0.0.1:55432/jobpulse'},
    {'REVET_PG_TEST_DSN': 'postgresql://revet_test:p@127.0.0.1:55432/revet_pg_test_ci?host=/var/run/postgresql'},
    {'REVET_PG_TEST_DSN': 'postgresql://revet_test:p@127.0.0.1:55432,10.0.0.5:55432/revet_pg_test_ci'},
])
def test_refuses_everything_else(overrides):
    with pytest.raises(PgTargetRefused):
        validate_target(_env(**overrides))


def _conn(row, tokens):
    conn = MagicMock()
    first, second = MagicMock(), MagicMock()
    first.mappings.return_value.one.return_value = row
    second.scalars.return_value.all.return_value = tokens
    conn.execute.side_effect = [first, second]
    return conn


GOOD_ROW = {'db': 'revet_pg_test_ci', 'usr': 'revet_test', 'addr': '127.0.0.1', 'port': 55432, 'ver': 160013}


def test_live_check_accepts_marked_pg16_loopback():
    assert verify_live_target(_conn(GOOD_ROW, ['revet_pg_test_ci']))['db'] == 'revet_pg_test_ci'


@pytest.mark.parametrize('change, tokens', [
    ({'addr': '10.0.0.5'}, ['revet_pg_test_ci']),
    ({'port': 5432}, ['revet_pg_test_ci']),
    ({'ver': 170002}, ['revet_pg_test_ci']),
    ({'usr': 'postgres'}, ['revet_pg_test_ci']),
    ({}, []),
    ({}, ['revet_pg_test_ci', 'revet_pg_test_ci']),
])
def test_live_check_refuses_unmarked_or_wrong_server(change, tokens):
    with pytest.raises(PgTargetRefused):
        verify_live_target(_conn({**GOOD_ROW, **change}, tokens))
