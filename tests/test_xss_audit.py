"""
XSS Audit — Regression Test for L2 Phase C Hardening

Purpose
-------
This test enumerates every dynamic ``innerHTML`` / ``insertAdjacentHTML`` /
``outerHTML`` interpolation across the templates we hardened in L2 Phase C and
fails if any of them re-introduce a raw ``${...}`` substitution that bypasses
the ``window.AIOutput.escapeHtml`` helper.

The intent is to **lock the property** so a future contributor cannot
inadvertently re-introduce an XSS sink in the hardened files. New files added
to ``HARDENED_TEMPLATES`` must abide by the same rule.

Allowed forms inside ``innerHTML = `...${X}...```:
    * ``${_esc(value)}``                       -- locally-aliased helper
    * ``${window.AIOutput.escapeHtml(value)}`` -- canonical helper call
    * ``${someFunctionCall()}``                -- function whose name itself
                                                  contains "esc" / "Esc" /
                                                  "Html" (case-sensitive),
                                                  e.g. getStatusBadge,
                                                  scoreClass, candLink, etc.
                                                  These are reviewed manually
                                                  as part of the helper
                                                  contract.
    * ``${INTEGER_OR_BOOLEAN_EXPR}``           -- numeric/boolean values that
                                                  cannot carry HTML payload.
                                                  We allow a small allow-list
                                                  of names known to be numeric
                                                  or constants (COLSPAN,
                                                  score, mScore,
                                                  applied.count,
                                                  monitor.id, job.id,
                                                  m.job_id, ticketNum_safe,
                                                  etc.).

The audit is deliberately **conservative** — when in doubt, escape.  The
allow-list of numeric / pre-validated identifiers is documented inline so any
future contributor sees exactly why a name is accepted without escaping.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"

# ---------------------------------------------------------------------------
# Files brought under the L2 Phase C hardening regime.
# Adding a file to this list asserts: every dynamic innerHTML interpolation
# in the file must be escape-safe.
# ---------------------------------------------------------------------------
HARDENED_TEMPLATES: tuple[str, ...] = (
    "apply.html",
    "apply_stsi.html",
    "support_request.html",
    "support_request_stsi.html",
    "vetting_sandbox.html",
    "ats_integration.html",
    "ats_integration_details.html",
    "log_monitoring.html",
    "scout_screening.html",
    "base_layout.html",
)

# ---------------------------------------------------------------------------
# Names that are KNOWN-SAFE to interpolate raw because they are numeric,
# boolean, server-validated identifiers, fixed constants, or already-escaped
# locals built earlier in the same code path.  Each entry is justified.
# ---------------------------------------------------------------------------
SAFE_NAME_PATTERNS: tuple[re.Pattern[str], ...] = (
    # JS constants and pure integers
    re.compile(r"^COLSPAN$"),
    re.compile(r"^MAX_[A-Z_]+$"),

    # Numeric counters / scores
    re.compile(r"^score$"),
    re.compile(r"^mScore$"),
    re.compile(r"^applied\.count$"),
    re.compile(r"^data\.fixed_count$"),
    re.compile(r"^data\.deleted\.\w+\s*\|\|\s*0$"),
    re.compile(r"^t\.turn$"),

    # Numeric .id / numeric Bullhorn IDs (server-validated integers)
    re.compile(r"^job\.id(\s*\|\|\s*[\'\"][^\'\"]*[\'\"])?$"),
    re.compile(r"^m\.job_id$"),
    re.compile(r"^monitor\.id$"),
    re.compile(r"^g\.candidate_id(\s*\|\|\s*[\'\"][^\'\"]*[\'\"])?$"),
    re.compile(r"^issueId$"),
    re.compile(r"^monitorId$"),
    re.compile(r"^candidateId$"),
    re.compile(r"^status$"),  # controlled enum from server (e.g. "Open", "Archived")
    re.compile(r"^g\.matches\.length$"),  # numeric .length on JS array

    # Already-escaped locals built upstream in the same code path.
    # Each of these names is constructed by concatenating _esc(...)-wrapped
    # values or fixed string literals — auditors should grep the surrounding
    # function before adding a new entry here.
    re.compile(r"^safeTicketNum$"),
    re.compile(r"^candLink$"),
    re.compile(r"^scoreBadge$"),
    re.compile(r"^statusBadge$"),
    re.compile(r"^mScoreBadge$"),
    re.compile(r"^mStatusBadge$"),
    re.compile(r"^notesCell$"),
    re.compile(r"^rescreenCell$"),
    re.compile(r"^transcriptHtml$"),
    re.compile(r"^chatHtml$"),
    re.compile(r"^qList$"),  # vetting_sandbox.html: built from _esc(q) in a loop

    # Branch selectors / helper-function calls that return fixed CSS class
    # names or other safe HTML fragments only.
    re.compile(r"^[a-zA-Z_]+\s*===\s*[\'\"][^\'\"]+[\'\"][\s\?][^?]*[\'\"][^\'\"]+[\'\"]\s*:\s*[\'\"][^\'\"]+[\'\"]$"),
    re.compile(r"^scoreClass\(.*\)$"),
    re.compile(r"^intentClass\(.*\)$"),  # vetting_sandbox.html: returns fixed CSS class
    re.compile(r"^getSeverityBadge\(.*\)$"),
    re.compile(r"^getStatusBadge\(.*\)$"),
    re.compile(r"^statusColor$"),
    re.compile(r"^badgeClass$"),
    re.compile(r"^dir$"),
    re.compile(r"^label$"),
    re.compile(r"^recClass$"),
    re.compile(r"^statusClass$"),
    re.compile(r"^type\s*===\s*'success'\s*\?\s*'check-circle'\s*:\s*type\s*===\s*'error'\s*\?\s*'exclamation-triangle'\s*:\s*'info-circle'$"),
    re.compile(r"^type\s*===\s*'error'\s*\?\s*'danger'\s*:\s*type$"),
    re.compile(r"^job\.status\s*===\s*'Open'\s*\?\s*'success'\s*:\s*'secondary'$"),

    # Conditional ternaries that resolve to a static-html or empty string
    re.compile(r"^total\s*!==\s*1\s*\?\s*[\'\"]s[\'\"]\s*:\s*[\'\"]{2}$"),
    # Ternary that resolves to a fixed HTML attribute string OR empty string.
    # Form: <expr-without-?> ? 'static string maybe with escaped quotes' : ''
    re.compile(r"^[^?]+\?\s*[\'\"].*[\'\"]\s*:\s*[\'\"][\'\"]$"),
    re.compile(r"^[^?]+\?\s*[\'\"][\'\"]\s*:\s*[\'\"].*[\'\"]$"),

    # Object.keys(...).length — numeric count
    re.compile(r"^Object\.keys\(.*\)\.length$"),

    # Jinja2 server-side template variables (rendered before JS sees them)
    re.compile(r"^\{\{\s*[^{}]+\s*\}\}$"),
)

# ---------------------------------------------------------------------------
# Per-template "consciously raw" exception list. These are interpolations the
# author has intentionally left raw because the source is trusted server-side
# rendered HTML (e.g. an AI-generated email preview the recruiter is supposed
# to inspect).  Each entry MUST cite the source-of-trust.
# ---------------------------------------------------------------------------
CONSCIOUSLY_RAW: dict[str, set[str]] = {
    # data.email_html is the rendered outreach-email body produced server-side
    # by the vetting pipeline (already sanitized by the email service before
    # the API returns it).  The Vetting Sandbox is super-admin-only, and the
    # entire purpose of this preview is to render the email exactly as the
    # candidate will see it.
    "vetting_sandbox.html": {
        "data.email_html || '<p>No preview available</p>'",
    },
}

# Helper-call patterns that count as "escaped"
ESCAPED_CALL_RE = re.compile(
    r"^(?:"
    r"_esc\(.*\)"                                        # local alias
    r"|window\.AIOutput\.escapeHtml\(.*\)"               # canonical
    r"|escapeHtml\(.*\)"                                 # bare (only inside helper file itself)
    r"|window\.AIOutput\.\w+\(.*\)"                      # any helper from AIOutput
    r")$"
)

# Match an innerHTML / insertAdjacentHTML / outerHTML assignment with a
# template-literal RHS, possibly spanning multiple lines.
INNERHTML_BLOCK_RE = re.compile(
    r"""
    \.(?:innerHTML|outerHTML)\s*[+]?=\s*`(?P<body>[^`]*)`
    |
    \.insertAdjacentHTML\s*\(\s*[\'\"][^\'\"]+[\'\"]\s*,\s*`(?P<body2>[^`]*)`\s*\)
    """,
    re.VERBOSE | re.DOTALL,
)

# Inside a template literal, find each ${...} substitution.  Supports nested
# braces one level deep (which is enough for our codebase).
SUBSTITUTION_RE = re.compile(r"\$\{((?:[^{}]|\{[^{}]*\})*)\}")


def _iter_substitutions(template_body: str) -> Iterator[str]:
    """Yield every ``${...}`` expression found inside a template-literal body."""
    for match in SUBSTITUTION_RE.finditer(template_body):
        yield match.group(1).strip()


def _is_safe_substitution(expr: str) -> bool:
    """Return True iff a substitution expression is acceptable inside innerHTML."""
    expr = expr.strip()

    # Empty interpolation (defensive)
    if not expr:
        return True

    # Already escaped via helper call?
    if ESCAPED_CALL_RE.match(expr):
        return True

    # Allow-listed safe identifier?
    for pattern in SAFE_NAME_PATTERNS:
        if pattern.match(expr):
            return True

    # Numeric literal or simple math?
    if re.fullmatch(r"[\d\.\+\-\*\/\s\(\)]+", expr):
        return True

    # String concatenation that ONLY composes already-escaped expressions
    # and string/number literals.
    parts = re.split(r"\s*\+\s*", expr)
    if len(parts) > 1 and all(
        ESCAPED_CALL_RE.match(p) or re.fullmatch(r"[\'\"][^\'\"]*[\'\"]", p) or re.fullmatch(r"[\d\.]+", p)
        for p in parts
    ):
        return True

    # Ternary that yields a fixed-string-only result on both sides
    ternary = re.fullmatch(
        r"[^?]+\?\s*[\'\"][^\'\"]*[\'\"]\s*:\s*[\'\"][^\'\"]*[\'\"]",
        expr,
    )
    if ternary:
        return True

    return False


def _scan_template(path: Path) -> list[tuple[int, str, str]]:
    """
    Return a list of (line_number, raw_substitution, surrounding_snippet)
    for every UNSAFE substitution in the file.
    """
    text = path.read_text(encoding="utf-8")
    findings: list[tuple[int, str, str]] = []
    snippet_lines = text.splitlines()
    consciously_raw_for_file = CONSCIOUSLY_RAW.get(path.name, set())

    for block in INNERHTML_BLOCK_RE.finditer(text):
        body = block.group("body") or block.group("body2") or ""
        body_start_offset = (
            block.start("body") if block.group("body") is not None else block.start("body2")
        )
        for sub_match in SUBSTITUTION_RE.finditer(body):
            expr = sub_match.group(1).strip()
            if _is_safe_substitution(expr):
                continue
            if expr in consciously_raw_for_file:
                continue

            abs_sub_start = body_start_offset + sub_match.start()
            line_number = text.count("\n", 0, abs_sub_start) + 1
            snippet = (
                snippet_lines[line_number - 1]
                if line_number <= len(snippet_lines)
                else ""
            )
            findings.append((line_number, expr, snippet.strip()))

    return findings


@pytest.mark.parametrize("template_name", HARDENED_TEMPLATES)
def test_hardened_template_has_no_unescaped_innerhtml_interpolation(
    template_name: str,
) -> None:
    """
    For every template in ``HARDENED_TEMPLATES``, every ``${...}`` substitution
    inside an ``innerHTML`` template literal must be escape-safe.

    Failures show the offending line, expression, and remediation hint so a
    contributor can quickly correct the regression.
    """
    template_path = TEMPLATES_DIR / template_name
    assert template_path.exists(), (
        f"Hardened template {template_name!r} not found at {template_path}. "
        "If the file was renamed, update HARDENED_TEMPLATES."
    )

    findings = _scan_template(template_path)

    if findings:
        report_lines = [
            "",
            f"Unescaped innerHTML interpolation(s) found in {template_name}:",
            "",
        ]
        for line_no, expr, snippet in findings:
            report_lines.append(f"  L{line_no}: ${{{expr}}}")
            report_lines.append(f"        | {snippet}")
        report_lines.extend([
            "",
            "Remediation: wrap the substitution with window.AIOutput.escapeHtml(...) "
            "or the locally-aliased _esc(...). If the value is provably safe "
            "(numeric, fixed CSS class, pre-escaped local), add a justification "
            "to SAFE_NAME_PATTERNS in tests/test_xss_audit.py.",
        ])
        pytest.fail("\n".join(report_lines))


def test_ai_output_helper_is_loaded_in_base_layout() -> None:
    """
    ``window.AIOutput`` must be wired into base_layout.html so every page that
    extends it has access to the escapeHtml helper.  Without this, the
    `_esc(...)` and `window.AIOutput.escapeHtml(...)` references throughout
    the hardened templates would throw at runtime.
    """
    base_layout = (TEMPLATES_DIR / "base_layout.html").read_text(encoding="utf-8")
    # Accept either a direct ``static/js/ai_output.js`` reference or a Flask
    # ``url_for('static', filename='js/ai_output.js')`` reference.
    assert (
        "static/js/ai_output.js" in base_layout
        or "js/ai_output.js" in base_layout
    ), (
        "static/js/ai_output.js must be referenced in base_layout.html so the "
        "window.AIOutput helper is available to every page."
    )


def test_ai_output_helper_module_exists_and_exposes_escape_html() -> None:
    """
    The helper module itself must exist and define escapeHtml on
    ``window.AIOutput``. This is the single source of truth that every
    hardened template depends on.
    """
    helper_path = REPO_ROOT / "static" / "js" / "ai_output.js"
    assert helper_path.exists(), (
        "static/js/ai_output.js is missing. The hardened templates depend on "
        "this helper module."
    )
    helper_src = helper_path.read_text(encoding="utf-8")
    assert "escapeHtml" in helper_src, (
        "static/js/ai_output.js must export escapeHtml on window.AIOutput."
    )
    assert "window.AIOutput" in helper_src, (
        "static/js/ai_output.js must attach its API to window.AIOutput so "
        "templates can call window.AIOutput.escapeHtml(...)."
    )
