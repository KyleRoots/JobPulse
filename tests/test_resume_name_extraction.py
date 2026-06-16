"""
Regression tests for resume-based candidate-name extraction.

Pinned by the April 2026 production failure:

    Bullhorn record 4648428 was created with firstName="Canadian" and
    lastName="Citizen" because the resume_parser.py heuristic rejected
    the actual name line ("Akhil Reddy, CTMP, MBA, ODCP, CBAP®") for
    failing ``word.isalpha()`` (commas + ®) and then accepted the next
    clean two-word line — "Canadian Citizen" — as the candidate's name.

These tests cover three layers of the fix:

  1. utils.candidate_name_extraction.WORK_AUTH_TOKENS / is_work_auth_phrase
     / is_valid_name — the blocklist that no name-extraction path may
     bypass.

  2. resume_parser._parse_text — the deterministic regex heuristic that
     now strips credentials before matching and runs every match
     through is_valid_name.

  3. End-to-end ResumeParser.parse_resume(file) — verifies the public
     API returns the right name on the exact Akhil Reddy resume shape.
"""
import io

import pytest

from utils.candidate_name_extraction import (
    WORK_AUTH_TOKENS,
    is_valid_name,
    is_work_auth_phrase,
)


# ---------------------------------------------------------------------------
# Layer 1 — blocklist + validator
# ---------------------------------------------------------------------------
class TestIsWorkAuthPhrase:
    def test_canadian_citizen(self):
        assert is_work_auth_phrase("Canadian Citizen") is True

    def test_us_citizen(self):
        assert is_work_auth_phrase("US Citizen") is True

    def test_permanent_resident(self):
        assert is_work_auth_phrase("Permanent Resident") is True

    def test_green_card(self):
        assert is_work_auth_phrase("Green Card") is True

    def test_h1b_visa_with_hyphen(self):
        # Tokenises on hyphens too.
        assert is_work_auth_phrase("H-1B Visa") is True

    def test_authorized_to_work(self):
        assert is_work_auth_phrase("Authorized to Work") is True

    def test_real_name_passes(self):
        assert is_work_auth_phrase("Akhil Reddy") is False

    def test_real_name_with_particles_passes(self):
        assert is_work_auth_phrase("Jean van der Berg") is False

    def test_empty_string(self):
        assert is_work_auth_phrase("") is False

    def test_none_input(self):
        assert is_work_auth_phrase(None) is False

    def test_case_insensitive(self):
        assert is_work_auth_phrase("CANADIAN CITIZEN") is True
        assert is_work_auth_phrase("canadian citizen") is True

    def test_blocklist_covers_common_visas(self):
        # Single-token blocklist covers unambiguous work-auth vocabulary.
        # "green" and "permanent" are deliberately NOT here — they are
        # ambiguous (real surnames) and only get rejected via the
        # phrase-level check below.
        for token in ("citizen", "resident", "visa", "h1b",
                      "ead", "opt", "naturalized"):
            assert token in WORK_AUTH_TOKENS, (
                f"Expected '{token}' in WORK_AUTH_TOKENS"
            )

    def test_ambiguous_tokens_not_in_token_blocklist(self):
        # Defence-in-depth: these are real surnames (Eva Green, etc.)
        # and must NOT be in the single-token blocklist.
        for token in ("green", "permanent", "authorized", "eligible",
                      "right", "card"):
            assert token not in WORK_AUTH_TOKENS, (
                f"Token '{token}' is ambiguous — must be phrase-only"
            )

    def test_phrases_catch_ambiguous_combinations(self):
        # The phrase rule must catch the work-auth combinations of
        # ambiguous tokens.
        assert is_work_auth_phrase("Green Card") is True
        assert is_work_auth_phrase("Permanent Resident") is True
        assert is_work_auth_phrase("Authorized to Work") is True


class TestRealSurnamesNotRejected:
    """Real people whose surname happens to overlap with a (now
    phrase-only) work-auth token must NOT be rejected by the validator.
    Regression guard against the original architect-flagged false
    positive risk (Eva Green, John Green, etc.)."""

    def test_eva_green_accepted(self):
        assert is_valid_name("Eva", "Green") is True

    def test_john_green_accepted(self):
        assert is_valid_name("John", "Green") is True

    def test_ceelo_green_accepted(self):
        assert is_valid_name("CeeLo", "Green") is True

    def test_unusual_permanent_surname_accepted(self):
        # "Permanent" alone is no longer a single-token rejector.
        assert is_valid_name("John", "Permanent") is True

    def test_green_first_name_accepted(self):
        assert is_valid_name("Green", "Smith") is True

    def test_eva_green_is_not_work_auth_phrase(self):
        assert is_work_auth_phrase("Eva Green") is False

    def test_john_permanent_is_not_work_auth_phrase(self):
        assert is_work_auth_phrase("John Permanent") is False


class TestIsValidNameWithBlocklist:
    """The validator must reject any (first, last) where the combined
    string trips the work-authorization blocklist."""

    def test_canadian_citizen_rejected(self):
        # The exact production failure.
        assert is_valid_name("Canadian", "Citizen") is False

    def test_us_citizen_rejected(self):
        assert is_valid_name("US", "Citizen") is False

    def test_permanent_resident_rejected(self):
        # Caught via single-token rule on "resident".
        assert is_valid_name("Permanent", "Resident") is False

    def test_green_card_holder_rejected(self):
        # Caught via phrase rule — "green card" is in WORK_AUTH_PHRASES.
        assert is_valid_name("Green", "Card Holder") is False

    def test_h1b_visa_rejected(self):
        assert is_valid_name("H1B", "Visa") is False

    def test_authorized_to_work_rejected(self):
        # Caught via phrase rule — both tokens are ambiguous on their
        # own but the phrase is unmistakable.
        assert is_valid_name("Authorized", "To Work") is False

    def test_real_name_accepted(self):
        assert is_valid_name("Akhil", "Reddy") is True

    def test_three_token_real_name_accepted(self):
        # Multi-word last names like "El Fared" still work.
        assert is_valid_name("Abderrahmane", "El Fared") is True


# ---------------------------------------------------------------------------
# Layer 2 — resume_parser._parse_text deterministic heuristic
# ---------------------------------------------------------------------------
class TestParseTextHeuristic:
    """Drives ResumeParser._parse_text directly with synthetic resume
    text to verify the credential-stripping + blocklist logic."""

    @pytest.fixture
    def parser(self):
        from resume_parser import ResumeParser
        return ResumeParser()

    def test_akhil_reddy_credentialed_name(self, parser):
        """The exact production-failure resume shape."""
        text = (
            "Akhil Reddy, CTMP, MBA, ODCP, CBAP\u00ae\n"
            "Email: akhil.reddy@example.com\n"
            "Toronto, ON\n"
            "(416) 555-1234\n"
            "Canadian Citizen\n"
            "\n"
            "PROFESSIONAL SUMMARY\n"
            "Senior consultant with 15 years of experience..."
        )
        result = parser._parse_text(text)
        assert result['first_name'] == 'Akhil'
        assert result['last_name'] == 'Reddy'

    def test_name_with_pmp_credential(self, parser):
        text = (
            "John Doe, PMP\n"
            "555-123-4567\n"
            "PROFESSIONAL EXPERIENCE\n"
        )
        result = parser._parse_text(text)
        assert result['first_name'] == 'John'
        assert result['last_name'] == 'Doe'

    def test_name_with_registered_symbol(self, parser):
        text = (
            "Jane Smith \u00ae\n"
            "jane@example.com\n"
        )
        result = parser._parse_text(text)
        assert result['first_name'] == 'Jane'
        assert result['last_name'] == 'Smith'

    def test_citizenship_line_alone_is_not_picked(self, parser):
        """If there is NO real name line above a citizenship marker,
        the heuristic must not silently accept the citizenship line."""
        text = (
            "Email: someone@example.com\n"
            "Phone: 555-555-5555\n"
            "Canadian Citizen\n"
            "\n"
            "PROFESSIONAL SUMMARY\n"
            "Experienced practitioner..."
        )
        result = parser._parse_text(text)
        # Email-derived fallback may fire ("Someone"), but the heuristic
        # must NOT have committed "Canadian"/"Citizen".
        assert result['first_name'] != 'Canadian'
        assert result['last_name'] != 'Citizen'

    def test_real_name_below_citizenship_line(self, parser):
        """Some resumes lead with status; the actual name comes after.
        The heuristic must skip the work-auth line and pick the real
        name later in the header."""
        text = (
            "US Citizen\n"
            "John Doe\n"
            "john.doe@example.com\n"
            "555-123-4567\n"
            "PROFESSIONAL SUMMARY\n"
        )
        result = parser._parse_text(text)
        assert result['first_name'] == 'John'
        assert result['last_name'] == 'Doe'

    def test_simple_two_word_name_still_works(self, parser):
        """Ensure the fix didn't regress the common happy path."""
        text = (
            "Jane Smith\n"
            "jane@example.com\n"
            "555-1234\n"
            "EXPERIENCE\n"
        )
        result = parser._parse_text(text)
        assert result['first_name'] == 'Jane'
        assert result['last_name'] == 'Smith'

    def test_three_word_name_with_middle(self, parser):
        text = (
            "Mary Jane Watson\n"
            "mary@example.com\n"
        )
        result = parser._parse_text(text)
        assert result['first_name'] == 'Mary'
        # last_name joins remaining tokens
        assert result['last_name'] in ('Jane Watson', 'Watson')

    def test_resume_word_is_skipped(self, parser):
        text = (
            "Resume\n"
            "Sarah Connor\n"
            "sarah@example.com\n"
        )
        result = parser._parse_text(text)
        assert result['first_name'] == 'Sarah'
        assert result['last_name'] == 'Connor'


# ---------------------------------------------------------------------------
# Layer 3 — lowercase name particles ("el-", "van", "de", ...)
# ---------------------------------------------------------------------------
class TestLowercaseParticleSurnames:
    """Pinned by a production finding: the old header heuristic required
    EVERY word to start with a capital letter
    (``all(word[0].isupper() ...)``). A surname with a lowercase particle —
    "Ahmed el-Gabry", "Ludwig van Beethoven", "Robert de Niro" — failed
    that check, so the real name line was skipped and the parser fell
    through to the next title-cased line (e.g. a "Career Highlights"
    section header), committing garbage like firstName="Career"
    lastName="Highlights".

    The fix makes the casing guard particle-aware and routes the split
    through the shared ``split_full_name`` helper, so the résumé header
    path now behaves exactly like the inbound-email extraction path.
    """

    @pytest.fixture
    def parser(self):
        from resume_parser import ResumeParser
        return ResumeParser()

    def _header(self, name):
        return (
            f"{name}\n"
            "Mobile: +1-613-222-0624\n"
            "E-mail: test@example.com\n"
            "\n"
            "Career Highlights\n"
            "- Senior Product Manager\n"
        )

    def test_lowercase_hyphen_particle_el_gabry(self, parser):
        """The exact failure shape: lowercase 'el-' particle."""
        result = parser._parse_text(self._header("Ahmed el-Gabry"))
        assert result['first_name'] == 'Ahmed'
        assert result['last_name'] == 'El-Gabry'

    def test_capitalized_particle_unchanged(self, parser):
        result = parser._parse_text(self._header("Ahmed El-Gabry"))
        assert result['first_name'] == 'Ahmed'
        assert result['last_name'] == 'El-Gabry'

    def test_all_caps_particle_normalized(self, parser):
        result = parser._parse_text(self._header("Ahmed EL-GABRY"))
        assert result['first_name'] == 'Ahmed'
        assert result['last_name'] == 'El-Gabry'

    def test_van_particle(self, parser):
        result = parser._parse_text(self._header("Ludwig van Beethoven"))
        assert result['first_name'] == 'Ludwig'
        assert result['last_name'] == 'van Beethoven'

    def test_de_particle(self, parser):
        result = parser._parse_text(self._header("Robert de Niro"))
        assert result['first_name'] == 'Robert'
        assert result['last_name'] == 'de Niro'

    def test_hyphen_and_apostrophe_name(self, parser):
        result = parser._parse_text(self._header("Mary-Jane O'Brien"))
        assert result['first_name'] == 'Mary-Jane'
        assert result['last_name'] == "O'Brien"

    def test_does_not_grab_section_header_when_particle_name_present(
        self, parser
    ):
        """Regression: the particle name line must be accepted FIRST so
        the parser never falls through to 'Career Highlights'."""
        result = parser._parse_text(self._header("Ahmed el-Gabry"))
        assert (result['first_name'], result['last_name']) != (
            'Career', 'Highlights'
        )

    def test_section_header_alone_is_not_picked(self, parser):
        """Defense-in-depth: even with NO real name line, a 'Career
        Highlights' section header must not be committed as a name."""
        text = (
            "E-mail: test@example.com\n"
            "Phone: 555-555-5555\n"
            "\n"
            "Career Highlights\n"
            "- Senior Product Manager\n"
        )
        result = parser._parse_text(text)
        assert result.get('first_name') != 'Career'
        assert result.get('last_name') != 'Highlights'

    def test_casing_is_normalized_via_shared_splitter(self, parser):
        """Documents the intentional casing policy. Because the résumé path
        now routes through the shared ``split_full_name`` helper (the SAME
        normalizer used by the inbound-email path), casing is title-cased
        rather than copied verbatim from the résumé:

          * all-caps "JOHN SMITH"          -> "John Smith"   (improvement)
          * lowercase particle "el-Gabry"  -> "El-Gabry"     (improvement)
          * internal-cap "McDonald"        -> "Mcdonald"     (tradeoff)

        The internal-cap flattening (McDonald/MacLeod/DeVito) is the one
        downside, accepted for cross-path consistency. Pinned here so the
        behavior is explicit and any future change is deliberate.
        """
        result = parser._parse_text(self._header("JOHN SMITH"))
        assert (result['first_name'], result['last_name']) == ('John', 'Smith')

        result = parser._parse_text(self._header("Ronald McDonald"))
        assert (result['first_name'], result['last_name']) == (
            'Ronald', 'Mcdonald'
        )

    def test_lowercase_non_particle_line_rejected(self, parser):
        """A fully lowercase non-name line must not be accepted as a
        name (the particle exception is narrow, not a blanket relaxation
        of the title-case requirement)."""
        text = (
            "experienced professional\n"
            "test@example.com\n"
        )
        result = parser._parse_text(text)
        assert result.get('first_name') != 'experienced'
        assert result.get('last_name') != 'professional'


# ---------------------------------------------------------------------------
# Layer 4 — backfill discovery query
# ---------------------------------------------------------------------------
class TestBackfillSearchQueryCoverage:
    """The backfill script's Bullhorn search query must surface BOTH:
      * single-token misnames (e.g. lastName="Citizen"), AND
      * phrase-only misnames (e.g. firstName="Green" lastName="Card")

    Regression guard against the architect-flagged gap: when we tightened
    WORK_AUTH_TOKENS by removing ambiguous words like "green" and
    "permanent", the backfill query stopped surfacing "Green Card" /
    "Permanent Resident" candidates because the query was built ONLY
    from WORK_AUTH_TOKENS. The fix derives extra search terms from
    WORK_AUTH_PHRASES and the local is_work_auth_phrase filter weeds
    out real-surname false positives after the fact.
    """

    def _import_backfill(self):
        # Import lazily inside the test so collection doesn't fail in
        # environments that lack the Flask app context (the script
        # imports `from app import app, get_bullhorn_service`).
        import importlib
        return importlib.import_module(
            "scripts.backfill_misnamed_candidates"
        )

    def test_phrase_derived_terms_include_ambiguous_words(self):
        m = self._import_backfill()
        derived = m._phrase_derived_search_terms()
        # Words from WORK_AUTH_PHRASES that are NOT in WORK_AUTH_TOKENS
        # but DO need to be searched for.
        for word in ("green", "permanent", "authorized", "eligible",
                     "card", "work", "permit"):
            assert word in derived, (
                f"Phrase-derived term '{word}' missing from search terms; "
                "backfill would miss phrase-only misnames."
            )

    def test_phrase_derived_terms_exclude_stopwords(self):
        m = self._import_backfill()
        derived = m._phrase_derived_search_terms()
        for stop in ("to", "the", "of", "for", "a", "an"):
            assert stop not in derived, (
                f"Stopword '{stop}' must not be a search term — would "
                "explode result count and add no signal."
            )

    def test_search_query_covers_single_token_misnames(self):
        m = self._import_backfill()
        query = m._build_search_query()
        # The exact production failure shape: lastName="Citizen".
        assert 'lastName:"citizen"' in query
        assert 'firstName:"citizen"' in query

    def test_search_query_covers_phrase_only_misnames(self):
        m = self._import_backfill()
        query = m._build_search_query()
        # Words derived from WORK_AUTH_PHRASES — without these the
        # backfill misses "Green Card" / "Permanent Resident" rows.
        for word in ("green", "permanent", "authorized"):
            assert f'firstName:"{word}"' in query, (
                f"Search query missing firstName clause for '{word}'"
            )
            assert f'lastName:"{word}"' in query, (
                f"Search query missing lastName clause for '{word}'"
            )
