"""
One-shot splitter for bullhorn_service.py → bullhorn_service/ package.

Uses ast to find exact byte ranges for each method, then splices them into
mixin files. The method source bytes are preserved character-for-character,
so behavior is identical.

Run from project root:  python scripts/_split_bullhorn_service.py
"""
import ast
import sys
from pathlib import Path

SRC = Path('bullhorn_service.py')
PKG = Path('bullhorn_service')

# (mixin_filename, mixin_class_name, [list of method names in this mixin])
MIXIN_LAYOUT = [
    ('auth.py', 'AuthMixin', [
        'authenticate', '_get_current_user_id', '_direct_login', 'test_connection',
    ]),
    ('jobs.py', 'JobsMixin', [
        'get_job_orders', 'get_tearsheet_jobs', 'get_jobs_by_query', 'get_job_by_id',
        'get_user_emails', 'compare_job_lists', 'get_jobs_batch', 'get_job_order',
        'update_job_order',
    ]),
    ('tearsheets.py', 'TearsheetsMixin', [
        'get_tearsheets', 'get_tearsheet_by_name', 'get_tearsheet_members',
        'remove_job_from_tearsheet', 'add_job_to_tearsheet',
        'add_candidate_to_tearsheet', 'remove_candidate_from_tearsheet',
    ]),
    ('candidates.py', 'CandidatesMixin', [
        'search_candidates', 'create_candidate', 'update_candidate',
        'upload_candidate_file', 'create_job_submission', 'get_candidate',
        'create_candidate_work_history', 'create_candidate_education',
    ]),
    ('notes.py', 'NotesMixin', [
        'get_candidate_notes', 'create_candidate_note',
    ]),
    ('entities.py', 'EntitiesMixin', [
        'get_entity', 'update_entity', 'search_entity', 'query_entity',
        'delete_entity', 'bulk_update_entities', 'bulk_delete_entities',
        'create_entity', 'add_entity_to_association', 'remove_entity_from_association',
        'get_entity_associations', 'query_entities', 'get_entity_files',
        'delete_entity_file', 'update_placement', 'update_client_contact',
        'update_client_corporation', 'update_corporate_user', 'get_entity_meta',
        'get_options', '_get_options_from_meta', 'get_settings',
    ]),
]

# Methods + class-level constants assigned to the core base class
CORE_METHODS = [
    '__init__', '_safe_json_parse', '_load_credentials', '_filter_excluded_jobs',
    'parse_address_string', 'normalize_job_address',
]


def main():
    source = SRC.read_text()
    tree = ast.parse(source)
    src_lines = source.splitlines(keepends=True)

    # Locate the BullhornService class
    cls_node = next(
        n for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name == 'BullhornService'
    )

    # Map method name → (start_line, end_line) using ast (1-indexed, inclusive)
    methods = {}
    constants = []  # list of (start_line, end_line) for class-level Assign nodes
    for node in cls_node.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Include preceding @decorator lines
            start = node.lineno
            if node.decorator_list:
                start = min(d.lineno for d in node.decorator_list)
            methods[node.name] = (start, node.end_lineno)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            constants.append((node.lineno, node.end_lineno))

    def slice_lines(start, end):
        # Convert 1-indexed inclusive range to 0-indexed slice
        return ''.join(src_lines[start - 1:end])

    # Verify every expected method exists
    expected = set(CORE_METHODS)
    for _, _, names in MIXIN_LAYOUT:
        expected.update(names)
    missing = expected - set(methods.keys())
    extra = set(methods.keys()) - expected
    if missing:
        sys.exit(f"FATAL: methods missing from layout: {sorted(missing)}")
    if extra:
        sys.exit(f"FATAL: methods present in source but not in layout: {sorted(extra)}")

    # Standard module header for every mixin file
    HEADER = (
        '"""{docstring}"""\n'
        'import os\n'
        'import json\n'
        'import logging\n'
        'from datetime import datetime, timedelta\n'
        'from typing import Dict, List, Optional, Any\n'
        'from urllib.parse import urlencode\n'
        '\n'
        'import requests  # noqa: F401  (used by methods via self.session)\n'
        '\n'
        'logger = logging.getLogger(__name__)\n'
        '\n\n'
    )

    # ---- _core.py: base class with constants + core helpers ----
    core_body = []
    # Class-level constants (preserve their source ordering)
    for s, e in sorted(constants):
        core_body.append(slice_lines(s, e))
    # Core methods
    for name in CORE_METHODS:
        s, e = methods[name]
        core_body.append(slice_lines(s, e))

    core_text = (
        HEADER.format(docstring='Core base for BullhornService — class constants and shared helpers.')
        + 'class _BullhornCore:\n'
        + '    """Base class holding constants, __init__, and shared low-level helpers."""\n'
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
            HEADER.format(docstring=f'{classname} — Bullhorn API methods for this domain.')
            + f'class {classname}:\n'
            + f'    """Mixin providing {filename[:-3]}-related Bullhorn API methods."""\n'
            + '\n'
            + ''.join(body_parts)
        )
        (PKG / filename).write_text(text)
        print(f"WROTE {PKG / filename}: {text.count(chr(10))} lines, {len(method_names)} methods")

    # ---- __init__.py: compose BullhornService from mixins ----
    init_text = '''"""
Scout Genius — BullhornService package.

This is the modular successor to the former bullhorn_service.py monolith
(2,610 lines, 58 methods). The class is composed from focused mixins:

  - _BullhornCore       : __init__, class constants, low-level helpers
  - AuthMixin           : OAuth/REST authentication + connection test
  - JobsMixin           : JobOrder retrieval, search, comparison
  - TearsheetsMixin     : Tearsheet CRUD + member management
  - CandidatesMixin     : Candidate CRUD, file upload, work history, education
  - NotesMixin          : Candidate note retrieval and creation
  - EntitiesMixin       : Generic entity CRUD + meta/options/settings

Public import surface is preserved:

    from bullhorn_service import BullhornService

All ~58 method signatures are unchanged. Behavior is identical: each method's
source bytes were spliced from the original file via AST.
"""
# Re-export `requests` at the package top-level so legacy test patches keep
# resolving against `bullhorn_service.requests.*` (e.g. the bullhorn_service.py
# monolith was patched via `mock.patch('bullhorn_service.requests.get')`). DO
# NOT REMOVE — see tests/test_service_bullhorn.py.
import requests  # noqa: F401

from bullhorn_service._core import _BullhornCore
from bullhorn_service.auth import AuthMixin
from bullhorn_service.jobs import JobsMixin
from bullhorn_service.tearsheets import TearsheetsMixin
from bullhorn_service.candidates import CandidatesMixin
from bullhorn_service.notes import NotesMixin
from bullhorn_service.entities import EntitiesMixin


class BullhornService(
    AuthMixin,
    JobsMixin,
    TearsheetsMixin,
    CandidatesMixin,
    NotesMixin,
    EntitiesMixin,
    _BullhornCore,
):
    """Service for interacting with Bullhorn ATS/CRM API.

    Composed from focused mixins — see bullhorn_service/__init__.py for layout.
    """
    pass


__all__ = ['BullhornService']
'''
    (PKG / '__init__.py').write_text(init_text)
    print(f"WROTE {PKG / '__init__.py'}")

    print("\nSUCCESS — all mixin files written.")


if __name__ == '__main__':
    main()
