"""
Regression tests for inbound email candidate extraction.

These tests pin the behaviour of the multi-layer fallback chain that
recovers a candidate's name + contact info even when the primary
subject regex and the AI resume parser both fail.

The Apr-2026 production failure (Abderrahmane EL Fared, job 34945, 3-token
name) is included verbatim as a fixture so the same bug class cannot
silently regress.
"""
import pytest

from utils.candidate_name_extraction import (
    extract_name_from_pattern,
    is_valid_name,
    is_valid_name_token,
    merge_name_candidates,
    parse_name_from_email_address,
    parse_name_from_filename,
    split_full_name,
    strip_html_to_text,
)


# ---------------------------------------------------------------------------
# split_full_name — the core multi-token splitter
# ---------------------------------------------------------------------------
class TestSplitFullName:
    def test_two_token_simple(self):
        assert split_full_name("John Smith") == ("John", "Smith")

    def test_three_token_with_middle(self):
        assert split_full_name("Mary Jane Smith") == ("Mary", "Jane Smith")

    def test_three_token_production_failure_case(self):
        """The exact case from the Apr 2026 production failure."""
        first, last = split_full_name("Abderrahmane El fared")
        assert first == "Abderrahmane"
        # particle "el" stays lowercase; surname "fared" gets title-cased
        assert last == "el Fared"

    def test_dutch_particles(self):
        assert split_full_name("Jean van der Berg") == ("Jean", "van der Berg")

    def test_hyphenated_first_name(self):
        assert split_full_name("Mary-Jane Smith") == ("Mary-Jane", "Smith")

    def test_apostrophe_surname(self):
        assert split_full_name("Sean O'Brien") == ("Sean", "O'Brien")

    def test_comma_format_last_first(self):
        assert split_full_name("Smith, John") == ("John", "Smith")

    def test_mononym_returns_first_only(self):
        first, last = split_full_name("Madonna")
        assert first == "Madonna"
        assert last is None

    def test_empty_string(self):
        assert split_full_name("") == (None, None)

    def test_garbage_input(self):
        assert split_full_name("$$$ ###") == (None, None)

    def test_mixed_case_normalized(self):
        assert split_full_name("john SMITH") == ("John", "Smith")


# ---------------------------------------------------------------------------
# extract_name_from_pattern — wraps split_full_name with a regex prefix
# ---------------------------------------------------------------------------
class TestExtractNameFromPattern:
    LINKEDIN_FAILED_SUBJECT = (
        "Cloud Network Engineer (34945) - Abderrahmane El fared has applied on LinkedIn"
    )

    def test_linkedin_three_token_subject_recovers_name(self):
        """REGRESSION: this exact subject failed in production on Apr 25 2026."""
        first, last = extract_name_from_pattern(self.LINKEDIN_FAILED_SUBJECT, r"-\s*")
        assert first == "Abderrahmane"
        assert last is not None and "Fared" in last

    def test_linkedin_two_token_still_works(self):
        first, last = extract_name_from_pattern(
            "Senior Engineer (12345) - John Smith has applied on LinkedIn", r"-\s*"
        )
        assert (first, last) == ("John", "Smith")

    def test_dice_with_nickname_parens(self):
        # The dice extractor strips "(Chris)" before this regex runs
        first, last = extract_name_from_pattern(
            "UX Designer (33633) - Christopher Huebner has applied", r"-\s*"
        )
        assert (first, last) == ("Christopher", "Huebner")

    def test_no_match_returns_none(self):
        assert extract_name_from_pattern("Random text with no name", r"-\s*") == (None, None)

    def test_html_label_prefix(self):
        first, last = extract_name_from_pattern("Name: Jane Q Public", r"Name[:\s]+")
        assert first == "Jane"
        assert "Public" in (last or "")


# ---------------------------------------------------------------------------
# parse_name_from_filename — Layer 3 fallback
# ---------------------------------------------------------------------------
class TestParseNameFromFilename:
    def test_failed_candidate_filename(self):
        """REGRESSION: 'EL Fared Abderrahmane Resume.docx' — the filename
        present in the production failure but unused by the old code."""
        first, last = parse_name_from_filename("EL Fared Abderrahmane Resume.docx")
        assert first is not None
        assert last is not None
        # Should recover all three name tokens (case-normalised, particle-aware)
        recovered = f"{first} {last}".lower()
        assert "abderrahmane" in recovered
        assert "fared" in recovered

    def test_first_last_resume_pdf(self):
        first, last = parse_name_from_filename("John Smith Resume.pdf")
        assert (first, last) == ("John", "Smith")

    def test_underscore_separated(self):
        first, last = parse_name_from_filename("John_Smith_CV.pdf")
        assert (first, last) == ("John", "Smith")

    def test_resume_prefix(self):
        first, last = parse_name_from_filename("Resume - John Smith.pdf")
        assert (first, last) == ("John", "Smith")

    def test_with_year_tag(self):
        first, last = parse_name_from_filename("John Smith Resume 2024.docx")
        assert (first, last) == ("John", "Smith")

    def test_with_version_tag(self):
        first, last = parse_name_from_filename("John_Smith_resume_v2.docx")
        assert (first, last) == ("John", "Smith")

    def test_generic_filename_returns_none(self):
        # No name in filename — shouldn't manufacture one
        assert parse_name_from_filename("resume.pdf") == (None, None)
        assert parse_name_from_filename("CV.docx") == (None, None)
        assert parse_name_from_filename("untitled.pdf") == (None, None)

    def test_empty_input(self):
        assert parse_name_from_filename("") == (None, None)
        assert parse_name_from_filename(None) == (None, None)  # type: ignore[arg-type]

    def test_hyphenated_name_in_filename(self):
        first, last = parse_name_from_filename("Mary-Jane O'Brien Resume.pdf")
        assert first == "Mary-Jane"
        assert last == "O'Brien"


# ---------------------------------------------------------------------------
# parse_name_from_email_address — Layer 3b fallback
# ---------------------------------------------------------------------------
class TestParseNameFromEmailAddress:
    def test_dot_separated(self):
        assert parse_name_from_email_address("john.smith@example.com") == ("John", "Smith")

    def test_underscore_separated(self):
        assert parse_name_from_email_address("john_smith@example.com") == ("John", "Smith")

    def test_trailing_digits_stripped(self):
        assert parse_name_from_email_address("john.smith24@example.com") == ("John", "Smith")

    def test_no_separator_returns_none(self):
        assert parse_name_from_email_address("johnsmith@example.com") == (None, None)

    def test_single_letter_initial_returns_none(self):
        # "j.smith@..." is too ambiguous to guess "J" as a first name
        assert parse_name_from_email_address("j.smith@example.com") == (None, None)

    def test_invalid_input(self):
        assert parse_name_from_email_address("") == (None, None)
        assert parse_name_from_email_address("not-an-email") == (None, None)


# ---------------------------------------------------------------------------
# is_valid_name — guards against generic/placeholder values
# ---------------------------------------------------------------------------
class TestIsValidName:
    def test_real_name_passes(self):
        assert is_valid_name("John", "Smith")
        assert is_valid_name("Abderrahmane", "El Fared")

    def test_none_strings_rejected(self):
        assert not is_valid_name("None", "None")
        assert not is_valid_name("none", "smith")

    def test_empty_rejected(self):
        assert not is_valid_name(None, "Smith")
        assert not is_valid_name("John", None)
        assert not is_valid_name("", "Smith")
        assert not is_valid_name("  ", "Smith")

    def test_generic_filename_words_rejected(self):
        assert not is_valid_name("Resume", "Smith")
        assert not is_valid_name("CV", "Doc")
        assert not is_valid_name("Candidate", "Application")

    def test_digits_rejected(self):
        assert not is_valid_name("John123", "Smith")
        assert not is_valid_name("John", "Smith2")

    def test_hyphenated_passes(self):
        assert is_valid_name("Mary-Jane", "O'Brien")


# ---------------------------------------------------------------------------
# merge_name_candidates — picks best from multiple sources
# ---------------------------------------------------------------------------
class TestMergeNameCandidates:
    def test_picks_first_valid_pair(self):
        result = merge_name_candidates(
            (None, None),
            ("None", "None"),
            ("John", "Smith"),
            ("Jane", "Doe"),
        )
        assert result == ("John", "Smith")

    def test_falls_back_to_partial(self):
        # No full pair available — accept first-name-only as last resort
        result = merge_name_candidates(
            (None, None),
            ("Madonna", None),
        )
        assert result == ("Madonna", None)

    def test_all_invalid_returns_none(self):
        result = merge_name_candidates(
            ("None", "None"),
            ("Resume", "Doc"),
            (None, None),
        )
        assert result == (None, None)


# ---------------------------------------------------------------------------
# strip_html_to_text — preserves Email:/Phone: labels for downstream regex
# ---------------------------------------------------------------------------
class TestStripHtmlToText:
    def test_plain_text_unchanged(self):
        assert strip_html_to_text("Email: foo@bar.com") == "Email: foo@bar.com"

    def test_strong_tags_stripped(self):
        text = strip_html_to_text("<p><strong>Email:</strong> foo@bar.com</p>")
        assert "Email:" in text
        assert "foo@bar.com" in text
        assert "<" not in text

    def test_br_becomes_newline(self):
        text = strip_html_to_text("Name: John<br>Email: john@example.com")
        assert "Name: John" in text
        assert "Email: john@example.com" in text

    def test_table_cells_separated(self):
        html = "<table><tr><td>Email:</td><td>foo@bar.com</td></tr></table>"
        text = strip_html_to_text(html)
        assert "Email:" in text
        assert "foo@bar.com" in text

    def test_empty_input(self):
        assert strip_html_to_text("") == ""
        assert strip_html_to_text(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# End-to-end: fallback chain on the production failure
# ---------------------------------------------------------------------------
class TestProductionFailureRegression:
    """Pins the full Apr 2026 failure scenario.

    The original failure: subject "Cloud Network Engineer (34945) -
    Abderrahmane El fared has applied on LinkedIn", filename "EL Fared
    Abderrahmane Resume.docx", AI resume parser returned name=None None.
    Without the fix, both name extractors produced None and the
    candidate was dropped. With the fix, EITHER the subject regex OR the
    filename parser must succeed.
    """

    SUBJECT = "Cloud Network Engineer (34945) - Abderrahmane El fared has applied on LinkedIn"
    FILENAME = "EL Fared Abderrahmane Resume.docx"

    def test_subject_extractor_alone_succeeds(self):
        first, last = extract_name_from_pattern(self.SUBJECT, r"-\s*")
        assert is_valid_name(first, last)
        assert first == "Abderrahmane"

    def test_filename_extractor_alone_succeeds(self):
        first, last = parse_name_from_filename(self.FILENAME)
        assert is_valid_name(first, last)

    def test_at_least_one_layer_recovers_the_candidate(self):
        """The contractual guarantee: one of the deterministic layers
        must produce a valid name for this candidate, with zero AI."""
        attempts = [
            extract_name_from_pattern(self.SUBJECT, r"-\s*"),
            parse_name_from_filename(self.FILENAME),
        ]
        winner = merge_name_candidates(*attempts)
        assert is_valid_name(*winner), (
            f"All deterministic layers failed for the production case. "
            f"Attempts: {attempts}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
