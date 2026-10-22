"""Placement-margin and client-onboarding polls must not log in every 10 seconds."""

import ast
import unittest
from pathlib import Path


SCHEDULER = Path(__file__).resolve().parents[1] / "scheduler_setup.py"
POLL_JOBS = ("placement_margin_poll", "client_ob_notify_poll")


def _poll_job_calls():
    tree = ast.parse(SCHEDULER.read_text())
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "add_job":
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        job_id = kwargs.get("id")
        if (
            isinstance(job_id, ast.Constant)
            and job_id.value in POLL_JOBS
        ):
            found[job_id.value] = kwargs
    return found


class BullhornPollIntervalTests(unittest.TestCase):
    def test_event_polls_run_every_two_minutes(self):
        jobs = _poll_job_calls()
        self.assertEqual(set(jobs), set(POLL_JOBS))
        for job_id, kwargs in jobs.items():
            trigger = kwargs["trigger"]
            self.assertIsInstance(trigger, ast.Call)
            self.assertEqual(trigger.func.id, "IntervalTrigger")
            trigger_kwargs = {kw.arg: kw.value for kw in trigger.keywords}
            self.assertEqual(trigger_kwargs["minutes"].value, 2, job_id)
            self.assertNotIn("seconds", trigger_kwargs, job_id)
            self.assertEqual(kwargs["max_instances"].value, 1, job_id)
            self.assertIs(kwargs["coalesce"].value, True, job_id)


if __name__ == "__main__":
    unittest.main()
