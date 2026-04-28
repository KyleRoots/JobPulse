"""CRUD on AutomationTask and AutomationLog rows used by the Automation Hub UI.

Part of the `automation_service` package — the monolithic
`automation_service.py` (1,839 lines) was split into focused mixins so
each cluster of related builtins lives next to its helpers.
"""
import json
import logging
import threading
import requests
from datetime import datetime, timedelta
from collections import defaultdict
from extensions import db
from models import AutomationTask, AutomationLog, AutomationChat

logger = logging.getLogger(__name__)


class TasksMixin:
    def get_all_logs(self, limit=50):
        return AutomationLog.query.order_by(
            AutomationLog.created_at.desc()
        ).limit(limit).all()

    def get_tasks(self, status=None):
        query = AutomationTask.query.order_by(AutomationTask.created_at.desc())
        if status:
            query = query.filter_by(status=status)
        return query.all()

    def get_task(self, task_id):
        return AutomationTask.query.get(task_id)

    def get_task_logs(self, task_id, limit=50):
        return AutomationLog.query.filter_by(
            automation_task_id=task_id
        ).order_by(AutomationLog.created_at.desc()).limit(limit).all()

    def delete_task(self, task_id):
        task = AutomationTask.query.get(task_id)
        if task:
            db.session.delete(task)
            db.session.commit()
            return True
        return False

    def update_task_status(self, task_id, status):
        task = AutomationTask.query.get(task_id)
        if task:
            task.status = status
            task.updated_at = datetime.utcnow()
            db.session.commit()
            return True
        return False

