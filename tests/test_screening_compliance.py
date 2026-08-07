"""Tests for Phase A screening compliance helpers."""

from screening.compliance import (
    PRIVACY_CONTACT_DISPLAY_LABEL,
    PRIVACY_CONTACT_MYTICAS,
    PRIVACY_CONTACT_STSI,
    SCREENING_RULES_VERSION,
    get_privacy_contact_display_label,
    get_privacy_contact_for_host,
    get_screening_rules_metadata,
)


class TestScreeningCompliance:
    def test_rules_version_is_set(self):
        assert SCREENING_RULES_VERSION

    def test_privacy_contact_by_host(self):
        # Myticas apply host → apply@myticas.com
        assert get_privacy_contact_for_host('apply.myticas.com') == PRIVACY_CONTACT_MYTICAS
        assert get_privacy_contact_for_host('www.apply.myticas.com') == PRIVACY_CONTACT_MYTICAS
        # STSI apply hosts → stsioffice@stsigroup.com (not apply@stsigroup.com)
        assert get_privacy_contact_for_host('apply.stsigroup.com') == PRIVACY_CONTACT_STSI
        assert get_privacy_contact_for_host('www.apply.stsigroup.com') == PRIVACY_CONTACT_STSI
        assert get_privacy_contact_for_host('APPLY.STSIGROUP.COM') == PRIVACY_CONTACT_STSI
        # Unknown / empty defaults to Myticas intake
        assert get_privacy_contact_for_host('') == PRIVACY_CONTACT_MYTICAS
        assert get_privacy_contact_for_host(None) == PRIVACY_CONTACT_MYTICAS

    def test_privacy_contact_display_label(self):
        assert get_privacy_contact_display_label() == 'Contact Us Here'
        assert PRIVACY_CONTACT_DISPLAY_LABEL == 'Contact Us Here'

    def test_privacy_mailto_addresses(self):
        assert PRIVACY_CONTACT_MYTICAS == 'apply@myticas.com'
        assert PRIVACY_CONTACT_STSI == 'stsioffice@stsigroup.com'
        assert PRIVACY_CONTACT_STSI != 'apply@stsigroup.com'

    def test_rules_metadata_includes_version(self):
        meta = get_screening_rules_metadata(service=None)
        assert meta['rules_version'] == SCREENING_RULES_VERSION
        assert meta['product_name']
        assert 'prompt_profile' in meta
        assert 'layer2_model' in meta
