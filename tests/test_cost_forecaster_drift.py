"""Drift detector — every active resolve_model('<site>') in production
code MUST be covered by `MODULE_DEFINITIONS` in services/cost_forecaster.

Without this guard, adding a new OpenAI call site without updating the
forecaster's module map would silently exclude it from cost projections.
"""
import os
import re
from pathlib import Path

from services.cost_forecaster import MODULE_DEFINITIONS

ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {'.git', '__pycache__', '.venv', 'venv', 'node_modules',
                '.pythonlibs', 'tests', 'attached_assets',
                'discovery_industrial_audit', 'alembic'}

# Sites intentionally not present in production code today but kept in
# MODULE_DEFINITIONS for backwards-compatible telemetry / legacy ids.
LEGACY_ALLOWLIST = {
    'scout_support.reopen-analysis',  # dashed legacy form, kept for old rows
}

# Site ids that appear in test fixtures / docstrings, not production calls.
PLACEHOLDER_SITES = {'<site>', '{site}', 'site_a', 'site_b'}

RESOLVE_RE = re.compile(r"resolve_model\(\s*['\"]([\w\.\-]+)['\"]")


def _iter_py_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fname in filenames:
            if fname.endswith('.py'):
                yield Path(dirpath) / fname


def test_every_production_resolve_model_site_is_in_module_definitions():
    mapped_sites = set()
    for m in MODULE_DEFINITIONS:
        mapped_sites.update(m.sites)

    discovered = set()
    for path in _iter_py_files():
        if path.name in {'cost_forecaster.py', 'openai_helper.py'}:
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for match in RESOLVE_RE.findall(text):
            if match in PLACEHOLDER_SITES:
                continue
            discovered.add(match)

    missing = sorted(discovered - mapped_sites - LEGACY_ALLOWLIST)
    assert not missing, (
        'Forecaster module map is missing these production call sites:\n  '
        + '\n  '.join(missing)
        + '\nAdd them to the appropriate ModuleDef.sites in '
          'services/cost_forecaster.py MODULE_DEFINITIONS.'
    )


def test_no_orphan_sites_in_module_definitions():
    """Sites in MODULE_DEFINITIONS should either appear in production code
    or be on the LEGACY_ALLOWLIST. Catches stale entries from removed
    features."""
    mapped_sites = set()
    for m in MODULE_DEFINITIONS:
        mapped_sites.update(m.sites)

    discovered = set()
    for path in _iter_py_files():
        if path.name in {'cost_forecaster.py', 'openai_helper.py'}:
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        for match in RESOLVE_RE.findall(text):
            discovered.add(match)

    orphans = sorted(mapped_sites - discovered - LEGACY_ALLOWLIST)
    assert not orphans, (
        'These sites are in MODULE_DEFINITIONS but not used in production '
        'code anywhere — remove them or add to LEGACY_ALLOWLIST:\n  '
        + '\n  '.join(orphans)
    )
