"""Gunicorn hooks: bind first, then start scheduler.

Railway healthchecks failed when --preload import ran ensure_background_services
(Bullhorn tearsheet load) before the process listened on $PORT.
"""


def post_fork(server, worker):
    try:
        from app import ensure_background_services
        ensure_background_services()
        server.log.info("post_fork: background services started (worker %s)", worker.pid)
    except Exception as exc:
        server.log.warning("post_fork: ensure_background_services skipped: %s", exc)
