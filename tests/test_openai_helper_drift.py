"""Drift detector — every `resolve_model('<site>', ...)` call site MUST
have a matching `log_call('<site>', ...)` (or `_log_call('<site>', ...)`)
in the same file. Without this guard, a future patch could add a new
OpenAI call site, instrument resolve_model, and silently forget the
log_call — leaving us blind to its cost.
"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {'.git', '__pycache__', '.venv', 'venv', 'node_modules',
                '.pythonlibs', 'tests', 'attached_assets', 'discovery_industrial_audit'}

RESOLVE_RE = re.compile(r"resolve_model\(\s*['\"]([\w\.\-]+)['\"]")
LOG_RE = re.compile(r"_?log_call\(\s*['\"]([\w\.\-]+)['\"]")


def _iter_py_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fname in filenames:
            if fname.endswith('.py'):
                yield Path(dirpath) / fname


def test_every_resolve_model_has_matching_log_call():
    missing = []
    for path in _iter_py_files():
        if path.name == 'openai_helper.py':
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='ignore')
        except OSError:
            continue
        resolves = set(RESOLVE_RE.findall(text))
        if not resolves:
            continue
        logs = set(LOG_RE.findall(text))
        for site in resolves:
            if site not in logs:
                missing.append(f'{path.relative_to(ROOT)}: resolve_model("{site}") with no matching log_call')

    assert not missing, (
        'Drift detected — every resolve_model() must be paired with log_call() '
        'in the same file:\n  ' + '\n  '.join(missing)
    )
