"""Primary-worker startup must register every scheduled job.

configure_scheduler_jobs imports CronTrigger at module scope and uses it
for the Monday Indeed leak digest. A later function-local import of the
same name makes CronTrigger local to the whole function, so the earlier
use raises UnboundLocalError and the worker never finishes registering
jobs. This test calls the real registrar so that class of failure fails CI.
"""

import ast
from pathlib import Path
from unittest.mock import patch

from apscheduler.triggers.cron import CronTrigger

SCHEDULER_PATH = Path(__file__).resolve().parents[1] / "scheduler_setup.py"

# These three are constructed with CronTrigger(...) rather than trigger='cron'.
# They are the call sites a shadowed CronTrigger name would blow up.
CRONTRIGGER_JOB_IDS = (
    "indeed_leak_digest",
    "indeed_leak_deep_sweep",
    "owner_reassignment_daily",
    "monthly_report_preview",
)


def _job_ids_declared_in_source():
    """Every add_job id= literal inside configure_scheduler_jobs."""
    tree = ast.parse(SCHEDULER_PATH.read_text())
    func = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "configure_scheduler_jobs"
    )
    ids = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        func_node = node.func
        if not isinstance(func_node, ast.Attribute) or func_node.attr != "add_job":
            continue
        for keyword in node.keywords:
            if keyword.arg == "id" and isinstance(keyword.value, ast.Constant):
                ids.append(keyword.value.value)
    return ids


class _RecordingScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, func=None, trigger=None, **kwargs):
        self.jobs.append({"func": func, "trigger": trigger, "id": kwargs.get("id")})


class _QuietVetting:
    """The registrar warms the active-job cache inline. Do not call Bullhorn."""

    def __init__(self, *args, **kwargs):
        pass

    def get_active_jobs_from_tearsheets(self):
        return []


def test_configure_scheduler_jobs_registers_every_primary_job(app):
    from app import db
    from models import GlobalSettings
    from scheduler_setup import configure_scheduler_jobs

    declared = _job_ids_declared_in_source()
    assert len(declared) == len(set(declared))
    assert "indeed_leak_sweep" in declared
    assert "indeed_leak_digest" in declared

    existing = GlobalSettings.query.filter_by(setting_key="next_sftp_upload_time").first()
    previous = existing.setting_value if existing else None
    scheduler = _RecordingScheduler()
    try:
        with patch(
            "candidate_vetting_service.CandidateVettingService",
            _QuietVetting,
        ):
            configure_scheduler_jobs(app, scheduler, is_primary_worker=True)
    finally:
        row = GlobalSettings.query.filter_by(setting_key="next_sftp_upload_time").first()
        if previous is None and row is not None:
            db.session.delete(row)
            db.session.commit()
        elif row is not None and previous is not None and row.setting_value != previous:
            row.setting_value = previous
            db.session.commit()

    registered = [job["id"] for job in scheduler.jobs]
    assert len(registered) == len(set(registered))
    assert set(registered) == set(declared)
    by_id = {job["id"]: job for job in scheduler.jobs}
    for job_id in declared:
        assert by_id[job_id]["func"] is not None
    for job_id in CRONTRIGGER_JOB_IDS:
        assert isinstance(by_id[job_id]["trigger"], CronTrigger)
    deep = by_id["indeed_leak_deep_sweep"]["trigger"]
    assert str(deep.timezone) == "America/New_York"
    assert deep.fields[5].expressions[0].first == 3
    assert deep.fields[6].expressions[0].first == 0
