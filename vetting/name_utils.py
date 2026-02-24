"""
Name parsing utility functions for candidate vetting.

Extracted from CandidateVettingService - these are pure functions
with no class state dependency.
"""


def parse_names(name_str):
    if not name_str:
        return []
    return [n.strip() for n in name_str.split(',') if n.strip()]


def parse_emails(email_str):
    if not email_str:
        return []
    return [e.strip() for e in email_str.split(',') if e.strip()]
