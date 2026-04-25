"""
Static validation tests for static/js/ai_output.js — the XSS-safe DOM helper module
loaded by base_layout.html.

These tests are intentionally static (file-content / regex assertions) rather than
DOM execution tests. The helper module is small (~180 LOC of pure DOM primitives)
and the value of executing it in a headless browser is low compared to the cost of
adding a Node/Playwright dependency to the pytest run. The properties enforced here
are:

  1. The file exists and is reachable from a Flask static URL.
  2. It exposes the documented public API on `window.AIOutput`.
  3. It does not itself use the unsafe primitives it is meant to replace
     (innerHTML assignment, document.write, eval, new Function, string-form
     setTimeout/setInterval).
  4. Its declared sanitization patterns reject the well-known dangerous URL schemes
     (javascript:, data:, vbscript:).
  5. base_layout.html actually loads the helper so descendants can call it.

If you change the public API surface in static/js/ai_output.js, update the
EXPECTED_PUBLIC_API list below.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = REPO_ROOT / "static" / "js" / "ai_output.js"
BASE_LAYOUT_PATH = REPO_ROOT / "templates" / "base_layout.html"

EXPECTED_PUBLIC_API = [
    "clear",
    "safeRenderText",
    "safeRenderAlert",
    "safeRenderDismissibleAlert",
    "safeRenderBadge",
    "safeRenderIconText",
    "safeAppendKeyValue",
    "safeRenderList",
    "safeBuildLink",
    "safeBuildHighlightedText",
    "escapeHtml",
    "isSafeUrl",
]

# Patterns that must NEVER appear inside the helper module — the whole point of the
# helper is to avoid them.
FORBIDDEN_IN_HELPER = [
    (re.compile(r"\.innerHTML\s*="), "Helper must not assign to .innerHTML"),
    (re.compile(r"\.outerHTML\s*="), "Helper must not assign to .outerHTML"),
    (re.compile(r"document\.write\s*\("), "Helper must not call document.write()"),
    (re.compile(r"\beval\s*\("), "Helper must not call eval()"),
    (re.compile(r"new\s+Function\s*\("), "Helper must not construct new Function()"),
    (
        re.compile(r"setTimeout\s*\(\s*['\"`]"),
        "Helper must not pass a string to setTimeout",
    ),
    (
        re.compile(r"setInterval\s*\(\s*['\"`]"),
        "Helper must not pass a string to setInterval",
    ),
    (re.compile(r"insertAdjacentHTML\s*\("), "Helper must not call insertAdjacentHTML"),
]

# Dangerous URL schemes the isSafeUrl validator must NOT permit. Each prefix is the
# lowercase shape an attacker would attempt — the validator is allow-list based and
# only accepts /, #, http://, https://, mailto:, tel:.
DANGEROUS_URL_PREFIXES = [
    "javascript:",
    "data:",
    "vbscript:",
    "file:",
    "blob:",
    "JavaScript:",  # case bypass
    " javascript:",  # leading whitespace bypass
]


def _strip_js_comments(src: str) -> str:
    """Strip /* block */ and // line comments from JS source. The 'forbidden
    primitive' assertions only care about executable code; documentation strings
    inside comments are allowed to mention `.innerHTML =` for explanatory purposes.
    Naive but sufficient for our helper file (no regex literals containing // or /*)."""
    # Block comments
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    # Line comments — guard against // appearing inside strings by only stripping
    # when // is preceded by start-of-line or whitespace. Our helper file has no
    # // appearing inside string literals.
    src = re.sub(r"(^|\s)//[^\n]*", r"\1", src)
    return src


@pytest.fixture(scope="module")
def helper_source() -> str:
    assert HELPER_PATH.exists(), f"Missing helper module at {HELPER_PATH}"
    return HELPER_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def helper_code(helper_source: str) -> str:
    """Helper source with comments stripped — for executable-code assertions."""
    return _strip_js_comments(helper_source)


@pytest.fixture(scope="module")
def base_layout_source() -> str:
    assert BASE_LAYOUT_PATH.exists(), f"Missing base layout at {BASE_LAYOUT_PATH}"
    return BASE_LAYOUT_PATH.read_text(encoding="utf-8")


def test_helper_file_exists_and_is_nonempty(helper_source: str) -> None:
    assert len(helper_source) > 500, "Helper module looks empty or truncated"
    assert "use strict" in helper_source, "Helper must declare 'use strict'"


def test_helper_exposes_full_public_api(helper_source: str) -> None:
    """Each documented helper must be attached to window.AIOutput."""
    for name in EXPECTED_PUBLIC_API:
        # Match either `name: name` (object-literal expose) or `AIOutput.name = name`.
        pattern = re.compile(
            rf"(?:^|[\s,{{])\b{name}\s*:\s*{name}\b|AIOutput\.{name}\s*="
        )
        assert pattern.search(helper_source), (
            f"Helper does not expose '{name}' on window.AIOutput. "
            "If the API was renamed, update EXPECTED_PUBLIC_API."
        )


def test_helper_defines_each_public_function(helper_source: str) -> None:
    """Each name must also have a function definition (not just be exposed)."""
    for name in EXPECTED_PUBLIC_API:
        pattern = re.compile(rf"function\s+{name}\s*\(")
        assert pattern.search(helper_source), (
            f"Helper exposes '{name}' but no `function {name}(...)` definition found."
        )


def test_helper_does_not_use_unsafe_primitives(helper_code: str) -> None:
    """The helper itself must avoid the very primitives it wraps. Comments are
    stripped so explanatory docstrings can mention the forbidden patterns."""
    for pattern, message in FORBIDDEN_IN_HELPER:
        match = pattern.search(helper_code)
        assert match is None, f"{message} (found: {match.group(0)!r})"


def test_helper_attaches_to_window_object(helper_source: str) -> None:
    assert "window.AIOutput" in helper_source, (
        "Helper must publish itself as window.AIOutput so templates can call it."
    )


def test_helper_string_coerces_inputs(helper_source: str) -> None:
    """Every text path must coerce via String() so non-string AI/Bullhorn payloads
    do not bypass textContent's escaping by being a Node or other DOM-like object."""
    assert "String(" in helper_source, (
        "Helper must coerce text inputs with String() to neutralize Node-like inputs."
    )


def test_url_validator_rejects_dangerous_schemes(helper_source: str) -> None:
    """Inspect the isSafeUrl source to confirm it is allow-list based, not deny-list."""
    # Locate the function body
    match = re.search(
        r"function\s+isSafeUrl\s*\([^)]*\)\s*\{(.*?)\n\s*\}",
        helper_source,
        re.DOTALL,
    )
    assert match, "Could not locate isSafeUrl function body"
    body = match.group(1)
    # The validator must be allow-list based: it must only return true after positive
    # checks against the safe scheme prefixes.
    must_contain = ["http://", "https://", "mailto:", "tel:"]
    for needle in must_contain:
        assert needle in body, (
            f"isSafeUrl must explicitly allow '{needle}' (allow-list)"
        )
    # And it must NOT contain any of the dangerous schemes (which would imply a
    # deny-list approach that's easily bypassed).
    for bad in ("javascript:", "data:", "vbscript:"):
        assert bad not in body, (
            f"isSafeUrl must not enumerate '{bad}' — use allow-list, not deny-list"
        )


def test_base_layout_includes_helper(base_layout_source: str) -> None:
    """The helper is useless if base_layout.html doesn't load it."""
    pattern = re.compile(r"static.*filename\s*=\s*['\"]js/ai_output\.js['\"]")
    assert pattern.search(base_layout_source), (
        "templates/base_layout.html must include static/js/ai_output.js via "
        "url_for('static', filename='js/ai_output.js')."
    )


def test_helper_loaded_after_bootstrap(base_layout_source: str) -> None:
    """Load order matters slightly less here since the helper is self-contained,
    but we want it loaded before any inline page <script> blocks that might call it."""
    bootstrap_idx = base_layout_source.find("bootstrap.bundle.min.js")
    helper_idx = base_layout_source.find("js/ai_output.js")
    assert bootstrap_idx >= 0, "Bootstrap include not found in base layout"
    assert helper_idx >= 0, "Helper include not found in base layout"
    assert helper_idx > bootstrap_idx, (
        "ai_output.js should be included after bootstrap so helper-using templates "
        "can rely on both."
    )


def test_class_name_sanitizer_uses_strict_charset(helper_source: str) -> None:
    """The class-name sanitization regex must be strict (a-zA-Z0-9 _ -)."""
    # The regex literal is at the top of the file
    assert re.search(r"CLASS_RE\s*=\s*/\[\^a-zA-Z0-9_\\\-\s\]/g", helper_source), (
        "CLASS_RE must be /[^a-zA-Z0-9_\\- ]/g to prevent attribute breakout"
    )


def test_target_sanitizer_blocks_javascript_pseudo(helper_source: str) -> None:
    """target attribute should only allow letter chars + underscore (_blank, _self)."""
    assert re.search(r"TARGET_RE\s*=\s*/\[\^a-zA-Z_\]/g", helper_source), (
        "TARGET_RE must restrict <a target> values to letters + underscore"
    )


def test_escape_html_maps_all_dangerous_characters(helper_source: str) -> None:
    """escapeHtml() must map the OWASP-recommended set of HTML/JS-attribute
    breakout characters: & < > " ' / ` =. Missing any one of these is a
    real-world XSS bypass risk in attribute or unquoted-tag contexts."""
    # Locate the escape map literal
    match = re.search(
        r"_escapeMap\s*=\s*\{([^}]+)\}",
        helper_source,
        re.DOTALL,
    )
    assert match, "Could not locate _escapeMap object literal"
    body = match.group(1)
    required_mappings = [
        ("'&'", "&amp;"),
        ("'<'", "&lt;"),
        ("'>'", "&gt;"),
        ('\'"\'', "&quot;"),
        ("'/'", "&#x2F;"),
        ("'`'", "&#x60;"),
        ("'='", "&#x3D;"),
    ]
    for src_char, escaped in required_mappings:
        assert src_char in body, f"_escapeMap missing source char {src_char}"
        assert escaped in body, f"_escapeMap missing escape sequence {escaped}"
    # Single-quote uses a different quoting style — accept either form
    assert "&#39;" in body or "&apos;" in body, (
        "_escapeMap must escape single quote to &#39; or &apos;"
    )


def test_escape_html_uses_global_replace_regex(helper_source: str) -> None:
    """The escape implementation must replace ALL occurrences (global flag), not
    just the first match — otherwise `<<script>` would only escape the first `<`."""
    # Look for the regex used inside escapeHtml. Must include the /g flag.
    match = re.search(
        r"function\s+escapeHtml\s*\([^)]*\)\s*\{(.*?)\n\s*\}",
        helper_source,
        re.DOTALL,
    )
    assert match, "Could not locate escapeHtml function body"
    body = match.group(1)
    # Confirm regex literal with g flag containing the dangerous char class.
    # Char class may contain escaped slashes (\/), so allow `\.` as well as
    # any non-`]`, non-`\` character.
    assert re.search(r"/\[(?:[^\]\\]|\\.)+\]/g", body), (
        "escapeHtml must use a regex with the /g flag to replace ALL occurrences"
    )
