"""The 2 AM OneDrive database backup must stay unscheduled."""

import ast
import unittest
from pathlib import Path


SCHEDULER = Path(__file__).resolve().parents[1] / "scheduler_setup.py"
ROUTE = Path(__file__).resolve().parents[1] / "routes" / "backup.py"


class NightlyBackupRetiredTests(unittest.TestCase):
    def test_scheduler_does_not_register_nightly_backup(self):
        tree = ast.parse(SCHEDULER.read_text())
        job_ids = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "add_job":
                continue
            for kw in node.keywords:
                if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                    job_ids.append(kw.value.value)
        self.assertNotIn("nightly_database_backup", job_ids)

    def test_manual_backup_route_does_not_start_a_backup(self):
        source = ROUTE.read_text()
        self.assertNotIn("BackupService", source)
        self.assertNotIn("threading", source)
        self.assertIn("410", source)
