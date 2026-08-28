import os
import logging
import threading

from flask import Flask, jsonify, redirect, url_for, request
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from sqlalchemy.orm import DeclarativeBase
from werkzeug.middleware.proxy_fix import ProxyFix
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import timedelta

from auth_policy import REMEMBER_ME_DAYS, SESSION_LIFETIME_HOURS


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)

login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access the Job Feed Portal.'

csrf = CSRFProtect()

def _limiter_storage_uri() -> str:
    """Prefer Redis in production so rate limits apply across Gunicorn workers."""
    explicit = (os.environ.get('RATE_LIMIT_STORAGE_URI') or '').strip()
    if explicit:
        return explicit
    redis_url = (os.environ.get('REDIS_URL') or os.environ.get('REDIS_PRIVATE_URL') or '').strip()
    if redis_url:
        if redis_url.startswith('redis://') or redis_url.startswith('rediss://'):
            return redis_url
        return f'redis://{redis_url}'
    return 'memory://'


def _warn_if_memory_limiter(env: str, storage_uri: str) -> None:
    if storage_uri != 'memory://':
        return
    if env == 'production' or (os.environ.get('APP_ENV') or '').lower() == 'production':
        logging.getLogger(__name__).warning(
            'Rate limiter using in-memory storage (per worker). '
            'Set REDIS_URL for shared login lockout across Gunicorn workers.'
        )


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=_limiter_storage_uri(),
)

PRODUCTION_DOMAINS = {'app.scoutgenius.ai', 'www.app.scoutgenius.ai', 'jobpulse.lyntrix.ai', 'www.jobpulse.lyntrix.ai'}

scheduler_started = False
scheduler_lock = threading.Lock()


def create_app():
    app = Flask(__name__)

    env = (os.environ.get('APP_ENV') or os.environ.get('ENVIRONMENT') or 'production').lower()
    app.config['ENVIRONMENT'] = env
    logging.getLogger(__name__).info(f"App environment set to: {env}")

    testing_mode = (
        os.environ.get('TESTING', '').lower() in ('1', 'true', 'yes', 'on')
        or os.environ.get('FLASK_ENV', '').lower() == 'testing'
    )

    app.secret_key = os.environ.get("SESSION_SECRET")
    if not app.secret_key and env == 'production' and not testing_mode:
        raise RuntimeError(
            'SESSION_SECRET is required in production. Set it in Railway variables.'
        )
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=SESSION_LIFETIME_HOURS)
    app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=REMEMBER_ME_DAYS)
    app.config['REMEMBER_COOKIE_SECURE'] = True
    app.config['REMEMBER_COOKIE_HTTPONLY'] = True

    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "pool_recycle": 120,
            "pool_pre_ping": True,
            "pool_size": 20,
            "max_overflow": 30,
            "pool_timeout": 30,
            "connect_args": {
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 5,
            }
        }
    else:
        app.logger.warning("DATABASE_URL not set, using default SQLite for development")
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///fallback.db"
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "connect_args": {"check_same_thread": False},
        }

    db.init_app(app)

    if not database_url:
        with app.app_context():
            from sqlalchemy import event
            @event.listens_for(db.engine, "connect")
            def _set_sqlite_wal(dbapi_conn, connection_record):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()

    login_manager.init_app(app)
    app.login_manager = login_manager

    limiter.init_app(app)
    _warn_if_memory_limiter(env, _limiter_storage_uri())

    @login_manager.unauthorized_handler
    def handle_unauthorized():
        is_ajax = (
            request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or request.accept_mimetypes.best_match(
                ['application/json', 'text/html']
            ) == 'application/json'
        )
        if is_ajax:
            return jsonify({
                'success': False,
                'message': 'Session expired. Please refresh the page and log in again.',
                'redirect': url_for('auth.login'),
            }), 401
        return redirect(url_for('auth.login', next=request.url))

    from sentry_config import init_sentry
    init_sentry(app)

    return app
