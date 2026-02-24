import os
import logging
import threading

from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from werkzeug.middleware.proxy_fix import ProxyFix
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import timedelta


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)

login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Please log in to access the Job Feed Portal.'

csrf = CSRFProtect()

PRODUCTION_DOMAINS = {'app.scoutgenius.ai', 'www.app.scoutgenius.ai', 'jobpulse.lyntrix.ai', 'www.jobpulse.lyntrix.ai'}

scheduler_started = False
scheduler_lock = threading.Lock()


def create_app():
    app = Flask(__name__)

    env = (os.environ.get('APP_ENV') or os.environ.get('ENVIRONMENT') or 'production').lower()
    app.config['ENVIRONMENT'] = env
    print(f"App environment set to: {env}")

    app.secret_key = os.environ.get("SESSION_SECRET")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
    app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)
    app.config['REMEMBER_COOKIE_SECURE'] = True
    app.config['REMEMBER_COOKIE_HTTPONLY'] = True

    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        app.config["SQLALCHEMY_DATABASE_URI"] = database_url
        app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "pool_recycle": 300,
            "pool_pre_ping": True,
            "pool_size": 20,
            "max_overflow": 30
        }
    else:
        app.logger.warning("DATABASE_URL not set, using default SQLite for development")
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///fallback.db"

    db.init_app(app)
    login_manager.init_app(app)
    app.login_manager = login_manager

    from sentry_config import init_sentry
    init_sentry(app)

    return app
