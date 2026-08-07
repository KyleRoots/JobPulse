"""Tests for Phase A screening compliance helpers."""

from screening.compliance import (
    SCREENING_RULES_VERSION,
    get_privacy_contact_for_host,
    get_screening_rules_metadata,
)


class TestScreeningCompliance:
    def test_rules_version_is_set(self):
        assert SCREENING_RULES_VERSION

    def test_privacy_contact_by_host(self):
        # Both apply hosts share EXO intake; apply@stsigroup.com is not provisioned.
        assert get_privacy_contact_for_host('apply.myticas.com') == 'apply@myticas.com'
        assert get_privacy_contact_for_host('apply.stsigroup.com') == 'apply@myticas.com'
        assert get_privacy_contact_for_host('www.apply.stsigroup.com') == 'apply@myticas.com'
        assert get_privacy_contact_for_host('') == 'apply@myticas.com'
        assert get_privacy_contact_for_host(None) == 'apply@myticas.com'

    def test_rules_metadata_includes_version(self):
        meta = get_screening_rules_metadata(service=None)
        assert meta['rules_version'] == SCREENING_RULES_VERSION
        assert meta['product_name']
        assert 'prompt_profile' in meta
        assert 'layer2_model' in meta
