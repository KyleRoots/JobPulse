"""Regression test for the Scout Prospector XSS hardening (L2 Phase A).

Two sinks in the Scout Prospector UI used to assign concatenated
strings — including raw URLs and AI-returned label values — to
``element.innerHTML``. That was a real XSS vector flagged by an
earlier architect review.

The fix (L2 Phase A, Apr 2026) replaced both with safe DOM
construction (``textContent``, ``createElement``, ``setAttribute``
with URL-scheme validation, ``replaceChildren``).

This test is a static guard: if a future contributor reintroduces
``innerHTML`` assignment in either of these <script> blocks, the
test fails immediately rather than waiting for a malicious AI
response or attacker-controlled URL to land in production.

Note: ``innerHTML`` assignments using only hardcoded literal
strings (e.g. ``btn.innerHTML = '<i class="..."></i>Loading...'``)
remain elsewhere in the same templates and are safe — they contain
no dynamic content. This test targets only the specific blocks
that previously consumed AI / external content.
"""
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read(template_relpath: str) -> str:
    return (PROJECT_ROOT / template_relpath).read_text(encoding='utf-8')


class TestProspectorDetailHiringActivity:
    """``hiringActivityText`` rewriter must use safe DOM construction."""

    @pytest.fixture
    def script(self) -> str:
        markup = _read('templates/scout_prospector_detail.html')
        # Isolate the IIFE block that targets hiringActivityText.
        marker = "getElementById('hiringActivityText')"
        assert marker in markup, "hiringActivityText script block missing"
        start = markup.index(marker)
        end = markup.index('</script>', start)
        return markup[start:end]

    def test_reads_textcontent_not_innerhtml(self, script: str):
        assert '.textContent' in script, (
            "hiringActivityText must be read via textContent so AI-generated "
            "markup is treated as text, not parsed as HTML."
        )

    def test_uses_safe_dom_construction(self, script: str):
        assert 'createElement' in script, (
            "hiringActivityText rewriter must build link nodes with "
            "createElement, not via innerHTML string concatenation."
        )
        assert 'replaceChildren' in script, (
            "hiringActivityText rewriter must use replaceChildren to "
            "swap content, not innerHTML assignment."
        )

    def test_validates_url_scheme(self, script: str):
        assert 'http:' in script and 'https:' in script, (
            "URLs extracted from AI text must be scheme-validated to "
            "block javascript:/data:/vbscript: payloads."
        )

    def test_no_innerhtml_assignment(self, script: str):
        # Allow .innerHTML reads only as part of a larger pattern; this
        # block should have neither read nor write of innerHTML now.
        assert 'innerHTML' not in script, (
            "hiringActivityText block must not touch innerHTML at all "
            "after L2 Phase A."
        )


class TestProspectorProfileFormRefineSuggestions:
    """``refineSuggestions`` renderer must use safe DOM construction."""

    @pytest.fixture
    def script(self) -> str:
        markup = _read('templates/scout_prospector_profile_form.html')
        marker = "getElementById('refineSuggestions')"
        assert marker in markup, "refineSuggestions script block missing"
        start = markup.index(marker)
        # The block ends at the next '.classList.remove' on refineResult,
        # which is the next statement after the swap.
        end = markup.index("getElementById('refineResult')", start)
        return markup[start:end]

    def test_uses_replace_children(self, script: str):
        assert 'replaceChildren' in script, (
            "refineSuggestions must use replaceChildren, not innerHTML "
            "assignment, when rendering AI-returned label arrays."
        )

    def test_no_innerhtml_assignment_in_render_block(self, script: str):
        assert 'innerHTML' not in script, (
            "refineSuggestions render block must not assign innerHTML "
            "after L2 Phase A — AI-returned suggestion strings can "
            "contain markup that would execute as script."
        )

    def test_uses_textcontent_for_label(self):
        # Verify the buildSuggestionRow helper exists and uses textContent
        markup = _read('templates/scout_prospector_profile_form.html')
        assert 'buildSuggestionRow' in markup, (
            "buildSuggestionRow helper must exist to construct safe DOM "
            "nodes for each AI suggestion row."
        )
        assert 'strong.textContent' in markup, (
            "Suggestion row labels must be set via textContent so any "
            "markup in AI-returned values is treated as text."
        )
