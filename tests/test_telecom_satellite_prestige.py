"""Telecom/satellite employer boost: list match only, no AI guessing."""
from screening.prestige import (
    detect_prestige_employer,
    detect_telecom_satellite_employer,
    resolve_employer_boost,
)


TELESAT_RESUME = """
Jane Doe
Ottawa, ON
Senior RF Engineer
Telesat Lightspeed, 2021-Present
Built Ka-band payload software for LEO constellation.
"""

BELL_RESUME = """
Alex Kim
Toronto, ON
Network Planner at Bell Canada (2019-present)
"""

CONSULTING_RESUME = """
Priya Shah
Consultant at Deloitte Canada, 2020-present
"""


def test_telesat_is_telecom_not_consulting():
    assert detect_telecom_satellite_employer(TELESAT_RESUME) == 'Telesat'
    assert detect_prestige_employer(TELESAT_RESUME) is None


def test_bell_canada_and_telus():
    assert detect_telecom_satellite_employer(BELL_RESUME) == 'Bell Canada'
    assert detect_telecom_satellite_employer(
        "Engineer, TELUS Communications, Vancouver"
    ) == 'TELUS'


def test_rogers_requires_company_phrase():
    assert detect_telecom_satellite_employer(
        "Network engineer at Rogers Communications"
    ) == 'Rogers'
    assert detect_telecom_satellite_employer(
        "Managed by John Rogers on the operations team"
    ) is None


def test_starlink_and_verizon():
    assert detect_telecom_satellite_employer("Current: Starlink, Seattle") == 'Starlink (SpaceX)'
    assert detect_telecom_satellite_employer("Verizon Wireless, Dallas") == 'Verizon'


def test_deloitte_stays_on_consulting_list():
    assert detect_prestige_employer(CONSULTING_RESUME) == 'Deloitte'
    assert detect_telecom_satellite_employer(CONSULTING_RESUME) is None


def test_boost_does_not_stack_and_respects_checkboxes():
    assert resolve_employer_boost('Deloitte', 'Telesat', True, True) == 'Deloitte'
    assert resolve_employer_boost(None, 'Telesat', True, True) == 'Telesat'
    assert resolve_employer_boost(None, 'Telesat', True, False) is None
    assert resolve_employer_boost('Deloitte', None, False, True) is None
