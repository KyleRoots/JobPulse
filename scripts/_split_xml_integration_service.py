"""
One-shot splitter for xml_integration_service.py → xml_integration_service/ package.

Uses ast to find exact byte ranges for each method, then splices them into
mixin files. The method source bytes are preserved character-for-character,
so behavior is identical.

Run from project root:  python scripts/_split_xml_integration_service.py
"""
import ast
import sys
from pathlib import Path

SRC = Path('xml_integration_service.py')
PKG = Path('xml_integration_service')
CLASS_NAME = 'XMLIntegrationService'

# (mixin_filename, mixin_class_name, [list of method names in this mixin])
MIXIN_LAYOUT = [
    ('mapping.py', 'MappingMixin', [
        'map_bullhorn_jobs_to_xml_batch',
        '_map_single_job_with_classification_result',
        'map_bullhorn_job_to_xml',
        '_generate_job_application_url',
        '_map_employment_type',
        '_map_remote_type',
        '_map_country_id_to_name',
        '_extract_assigned_recruiter',
        '_map_recruiter_to_linkedin_tag',
        '_clean_description',
        '_format_date',
    ]),
    ('validation.py', 'ValidationMixin', [
        '_validate_job_data',
        '_verify_job_added_to_xml',
        '_verify_job_exists_in_xml',
        '_verify_job_update_in_xml',
        '_check_if_update_needed',
        '_compare_job_fields',
    ]),
    ('file_ops.py', 'FileOpsMixin', [
        '_clean_extra_whitespace',
        'sort_xml_jobs_by_date',
        '_cleanup_old_backups',
        '_safe_write_xml',
    ]),
    ('jobs.py', 'JobsMixin', [
        'add_job_to_xml',
        'remove_job_from_xml',
        '_update_fields_in_place',
        'update_job_in_xml',
        'regenerate_xml_from_jobs',
        'perform_comprehensive_field_sync',
    ]),
    ('sync.py', 'SyncMixin', [
        'detect_orphaned_jobs',
        'remove_orphaned_jobs',
        'sync_xml_with_bullhorn_jobs',
    ]),
]

# __init__, the 2 static formatters, plus any class-level constants stay in core.
CORE_METHODS = [
    'format_linkedin_recruiter_tag',
    'sanitize_linkedin_recruiter_tag',
    '__init__',
]


def main():
    if not SRC.exists():
        sys.exit(f"FATAL: source file {SRC} not found")
    PKG.mkdir(exist_ok=True)

    source = SRC.read_text()
    tree = ast.parse(source)
    src_lines = source.splitlines(keepends=True)

    cls_node = next(
        n for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == CLASS_NAME
    )

    # Map method name → (start_line, end_line) using ast (1-indexed, inclusive)
    methods = {}
    constants = []
    for node in cls_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            if node.decorator_list:
                start = min(d.lineno for d in node.decorator_list)
            methods[node.name] = (start, node.end_lineno)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            constants.append((node.lineno, node.end_lineno))

    def slice_lines(start, end):
        return ''.join(src_lines[start - 1:end])

    # Verify every expected method exists, and no method is missing from layout
    expected = set(CORE_METHODS)
    for _, _, names in MIXIN_LAYOUT:
        expected.update(names)
    missing = expected - set(methods.keys())
    extra = set(methods.keys()) - expected
    if missing:
        sys.exit(f"FATAL: methods missing from source: {sorted(missing)}")
    if extra:
        sys.exit(f"FATAL: methods present in source but not in layout: {sorted(extra)}")

    # Standard module header for every mixin file. Includes every name the
    # original module-level code touched, so spliced method bodies resolve
    # all references unchanged.
    HEADER = (
        '"""{docstring}"""\n'
        'import os\n'
        'import logging\n'
        'import re\n'
        'import shutil\n'
        'import time\n'
        'import threading\n'
        'import urllib.parse\n'
        'import html\n'
        'from typing import Dict, List, Optional\n'
        'from datetime import datetime\n'
        'try:\n'
        '    from lxml import etree\n'
        'except ImportError:\n'
        '    from xml_safe_compat import safe_etree as etree\n'
        'from xml_processor import XMLProcessor\n'
        'from job_classification_service import JobClassificationService, InternalJobClassifier\n'
        'from xml_safeguards import XMLSafeguards\n'
        'from tearsheet_config import TearsheetConfig\n'
        'from utils.field_mappers import map_employment_type, map_remote_type\n'
        '\n'
        'logger = logging.getLogger(__name__)\n'
        '\n\n'
    )

    # ---- _core.py: base class with constants + __init__ + static formatters ----
    core_body = []
    for s, e in sorted(constants):
        core_body.append(slice_lines(s, e))
    for name in CORE_METHODS:
        s, e = methods[name]
        core_body.append(slice_lines(s, e))

    core_text = (
        HEADER.format(docstring='Core base for XMLIntegrationService — __init__, class constants, and shared static helpers.')
        + 'class _XMLCore:\n'
        + '    """Base class holding __init__ and stateless LinkedIn formatters.\n'
        + '\n'
        + '    The static methods (format_linkedin_recruiter_tag, sanitize_linkedin_recruiter_tag)\n'
        + '    are kept on the core class so they remain accessible as\n'
        + '    XMLIntegrationService.format_linkedin_recruiter_tag(...) for any\n'
        + '    consumer that calls them via the class rather than an instance.\n'
        + '    """\n'
        + '\n'
        + ''.join(core_body)
    )
    (PKG / '_core.py').write_text(core_text)
    print(f"WROTE {PKG / '_core.py'}: {core_text.count(chr(10))} lines")

    # ---- Each mixin file ----
    for filename, classname, method_names in MIXIN_LAYOUT:
        body_parts = []
        for name in method_names:
            s, e = methods[name]
            body_parts.append(slice_lines(s, e))
        text = (
            HEADER.format(docstring=f'{classname} — XMLIntegrationService methods for this domain.')
            + f'class {classname}:\n'
            + f'    """Mixin providing {filename[:-3]}-related XMLIntegrationService methods."""\n'
            + '\n'
            + ''.join(body_parts)
        )
        (PKG / filename).write_text(text)
        print(f"WROTE {PKG / filename}: {text.count(chr(10))} lines, {len(method_names)} methods")

    # ---- __init__.py: compose XMLIntegrationService from mixins ----
    init_text = '''"""
Scout Genius — XMLIntegrationService package.

This is the modular successor to the former xml_integration_service.py monolith
(2,241 lines, 35 methods). The class is composed from focused mixins:

  - _XMLCore           : __init__, static LinkedIn formatters
  - MappingMixin       : Bullhorn job dict → XML field mapping + cleaners
  - ValidationMixin    : pre/post-write validation + change detection
  - FileOpsMixin       : safe write, backup rotation, sort, whitespace cleanup
  - JobsMixin          : add/remove/update single job, regenerate full feed
  - SyncMixin          : full feed sync + orphan detection/removal

Public import surface is preserved:

    from xml_integration_service import XMLIntegrationService

All 35 method signatures are unchanged. Behavior is identical: each method's
source bytes were spliced from the original file via AST.
"""
from xml_integration_service._core import _XMLCore
from xml_integration_service.mapping import MappingMixin
from xml_integration_service.validation import ValidationMixin
from xml_integration_service.file_ops import FileOpsMixin
from xml_integration_service.jobs import JobsMixin
from xml_integration_service.sync import SyncMixin


class XMLIntegrationService(
    SyncMixin,
    JobsMixin,
    MappingMixin,
    ValidationMixin,
    FileOpsMixin,
    _XMLCore,
):
    """Service for integrating Bullhorn job data with XML files.

    Composed from focused mixins — see xml_integration_service/__init__.py for layout.
    """
    pass


__all__ = ['XMLIntegrationService']
'''
    (PKG / '__init__.py').write_text(init_text)
    print(f"WROTE {PKG / '__init__.py'}")

    print("\nSUCCESS — all mixin files written.")


if __name__ == '__main__':
    main()
