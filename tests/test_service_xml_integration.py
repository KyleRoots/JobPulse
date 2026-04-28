"""
XMLIntegrationService package-surface regression tests.

Locks in the public contract that consumers depend on after the
xml_integration_service.py monolith → xml_integration_service/ package split.
"""


class TestXMLIntegrationPackageSurface:
    def test_xmlintegrationservice_importable_from_package_root(self):
        """The 6 consumer files do `from xml_integration_service import
        XMLIntegrationService`. That contract must stay stable."""
        from xml_integration_service import XMLIntegrationService
        assert XMLIntegrationService is not None
        assert isinstance(XMLIntegrationService, type)
        # Instantiation must succeed without external dependencies
        svc = XMLIntegrationService()
        assert svc is not None

    def test_xmlintegrationservice_mro_has_no_duplicate_methods(self):
        """If two mixins ever define the same method, MRO silently picks one
        and the other becomes dead code. Catch that early."""
        from xml_integration_service import XMLIntegrationService
        from xml_integration_service._core import _XMLCore
        from xml_integration_service.mapping import MappingMixin
        from xml_integration_service.validation import ValidationMixin
        from xml_integration_service.file_ops import FileOpsMixin
        from xml_integration_service.jobs import JobsMixin
        from xml_integration_service.sync import SyncMixin

        seen = {}
        collisions = []
        for mixin in (_XMLCore, MappingMixin, ValidationMixin,
                      FileOpsMixin, JobsMixin, SyncMixin):
            for name, val in vars(mixin).items():
                if name.startswith('__') or not callable(val):
                    continue
                if name in seen:
                    collisions.append(
                        f"{name}: {seen[name].__name__} vs {mixin.__name__}"
                    )
                else:
                    seen[name] = mixin

        assert collisions == [], (
            "Mixin method-name collisions detected (one shadows the other):\n  "
            + "\n  ".join(collisions)
        )
        assert XMLIntegrationService.__mro__[-1] is object

    def test_all_critical_methods_resolve_through_composition(self):
        """Spot-check that one method from each mixin actually resolves on the
        composed class — guards against a mixin being dropped from __init__.py."""
        from xml_integration_service import XMLIntegrationService
        svc = XMLIntegrationService()
        # _XMLCore (static)
        assert callable(getattr(XMLIntegrationService,
                                'format_linkedin_recruiter_tag'))
        # MappingMixin
        assert callable(getattr(svc, 'map_bullhorn_job_to_xml'))
        # ValidationMixin
        assert callable(getattr(svc, '_validate_job_data'))
        # FileOpsMixin
        assert callable(getattr(svc, '_safe_write_xml'))
        # JobsMixin
        assert callable(getattr(svc, 'add_job_to_xml'))
        # SyncMixin
        assert callable(getattr(svc, 'sync_xml_with_bullhorn_jobs'))

    def test_static_linkedin_formatter_behavior_preserved(self):
        """End-to-end behavior smoke test on a stateless utility — a quick
        guard against the splitter ever corrupting method bodies."""
        from xml_integration_service import XMLIntegrationService
        # Valid input
        tag = XMLIntegrationService.format_linkedin_recruiter_tag(
            [{'linkedInCompanyID': 12345}]
        )
        assert tag == '#LI-12345'
        # Empty input
        assert XMLIntegrationService.format_linkedin_recruiter_tag([]) == ''
        assert XMLIntegrationService.format_linkedin_recruiter_tag(None) == ''
        # Sanitizer strips trailing junk
        sanitized = XMLIntegrationService.sanitize_linkedin_recruiter_tag(
            '#LI-99 :Some Recruiter Name'
        )
        assert sanitized == '#LI-99'
