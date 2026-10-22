"""Bullhorn Share links replace the unavailable OpenWindow.cfm address."""

import unittest

from utils.bullhorn_helpers import bullhorn_record_url


class BullhornRecordUrlTests(unittest.TestCase):
    def test_matches_the_share_link_button(self):
        self.assertEqual(
            bullhorn_record_url("Candidate", 4584046),
            "https://app.bullhornstaffing.com/openwindow?entity=Candidate&id=4584046",
        )

    def test_jobs_and_companies_use_the_same_host(self):
        self.assertEqual(
            bullhorn_record_url("JobOrder", "34990"),
            "https://app.bullhornstaffing.com/openwindow?entity=JobOrder&id=34990",
        )
        self.assertEqual(
            bullhorn_record_url("ClientCorporation", 1001),
            "https://app.bullhornstaffing.com/openwindow?entity=ClientCorporation&id=1001",
        )

    def test_blank_id_does_not_build_a_link(self):
        self.assertEqual(bullhorn_record_url("Candidate", None), "")
        self.assertEqual(bullhorn_record_url("Candidate", ""), "")
        self.assertEqual(bullhorn_record_url("", 12), "")


if __name__ == "__main__":
    unittest.main()
