"""Board display titles and the mailbox id that still depends on them.

Job 79463 is the Richmond sorter from the Oct 12 visibility review.
"""
import logging

import pytest
from lxml import etree

from email_inbound_service._core import _InboundCore
from email_inbound_service.extraction_mixin import ExtractionMixin
from email_inbound_service.resume_mixin import ResumeMixin
from feeds.board_titles import zip_display_title
from xml_integration_service import XMLIntegrationService
from xml_integration_service.mapping import MappingMixin


JOB_ID = 79463
CANONICAL = "General Labor / Sorter / Order Picker"
DESCRIPTION = (
    "<p>What You'll Do</p><ul><li>Pick customer orders</li>"
    "<li>Verify products, quantities, and item numbers</li></ul>"
)


class _Parser(ResumeMixin, ExtractionMixin, _InboundCore):
    def __init__(self):
        self.logger = logging.getLogger("test_board_job_titles")
        self.openai_client = None


def _job(**overrides):
    data = {
        "id": JOB_ID,
        "title": CANONICAL,
        "publicDescription": DESCRIPTION,
        "address": {"city": "Midlothian", "state": "VA", "countryName": "United States"},
        "employmentType": "Contract To Hire",
    }
    data.update(overrides)
    return data


def _map(svc, source_channel, title=CANONICAL):
    return svc.map_bullhorn_job_to_xml(
        _job(title=title),
        existing_reference_number="ZR79463REF",
        skip_ai_classification=True,
        existing_ai_fields={
            "jobfunction": "Operations",
            "jobindustries": "Warehousing",
            "senioritylevel": "Entry level",
        },
        source_channel=source_channel,
    )


@pytest.fixture(scope="module")
def svc():
    return XMLIntegrationService()


class TestZipDisplayTitle:
    def test_representative_job_keeps_slashes_and_id(self):
        assert zip_display_title(CANONICAL, JOB_ID) == (
            "General Labor / Sorter / Order Picker (79463)"
        )

    def test_punctuation_unicode_and_numbers_stay(self):
        title = 'R&D – "Côte" / Shift 2 (PE)'
        assert zip_display_title(title, JOB_ID) == f"{title} (79463)"

    def test_hyphen_and_em_dash_stay(self):
        assert zip_display_title("Order-Picker — Nights", 10001) == (
            "Order-Picker — Nights (10001)"
        )

    def test_does_not_append_a_second_suffix(self):
        assert zip_display_title(f"{CANONICAL} (79463)", JOB_ID) == (
            f"{CANONICAL} (79463)"
        )

    def test_empty_and_malformed(self):
        assert zip_display_title(None, JOB_ID) == "Untitled Position (79463)"
        assert zip_display_title("   ", JOB_ID) == "Untitled Position (79463)"
        assert zip_display_title(CANONICAL, None) == CANONICAL
        assert zip_display_title(CANONICAL, "None") == CANONICAL

    def test_html_entities_decode_and_cdata_terminator_is_split(self):
        assert zip_display_title("A &amp; B", 10) == "A & B (10)"
        assert "]]>" not in zip_display_title("bad]]>title", 10)
        assert zip_display_title("bad]]>title", 10) == "bad]] >title (10)"

    def test_control_characters_dropped(self):
        assert zip_display_title("Pick\x00er / Pack", 10) == "Picker / Pack (10)"

    def test_cdata_round_trip_keeps_markup_characters_as_text(self):
        original = """A & B <tag> > end "quoted" O'Brien / Sorter"""
        expected = f"{original} ({JOB_ID})"
        title = zip_display_title(original, JOB_ID)
        assert title == expected
        root = etree.Element("job")
        node = etree.SubElement(root, "title")
        node.text = etree.CDATA(f" {title} ")
        xml = etree.tostring(root, encoding="unicode")
        parsed = etree.fromstring(xml.encode("utf-8"))
        assert parsed.find("title").text.strip() == expected
        for character in ("&", "<", ">", '"', "'"):
            assert character in parsed.find("title").text


class TestFeedTitlesByBoard:
    def test_zip_and_indeed_xml_keep_punctuation_linkedin_does_not(self, svc):
        zip_job = _map(svc, "ZipRecruiter")
        linkedin = _map(svc, "LinkedIn")
        indeed = _map(svc, "Indeed")
        default = _map(svc, "Dice")

        kept = "General Labor / Sorter / Order Picker (79463)"
        assert zip_job["title"] == kept
        assert indeed["title"] == kept
        assert zip_job["bhatsid"] == indeed["bhatsid"] == linkedin["bhatsid"] == "79463"
        assert (
            zip_job["referencenumber"]
            == indeed["referencenumber"]
            == linkedin["referencenumber"]
            == "ZR79463REF"
        )
        assert zip_job["url"].split("?")[0] == indeed["url"].split("?")[0]
        assert indeed["url"].split("?")[0] == linkedin["url"].split("?")[0]
        assert linkedin["url"].split("?")[0] == default["url"].split("?")[0]
        assert zip_job["url"].endswith("?source=ZipRecruiter")
        assert indeed["url"].endswith("?source=Indeed")
        assert linkedin["url"].endswith("?source=LinkedIn")
        assert "/79463/" in zip_job["url"]
        # Slash-to-space keeps the spaces that were already beside the slash.
        historical = "General Labor   Sorter   Order Picker (79463)"
        assert linkedin["title"] == historical
        assert default["title"] == historical
        assert linkedin["description"] == zip_job["description"] == indeed["description"]

    def test_unicode_numbers_and_multiple_parentheses(self, svc):
        title = 'Café / Sorter – Shift 2 (PE) (2024)'
        zip_job = _map(svc, "ZipRecruiter", title=title)
        indeed = _map(svc, "Indeed", title=title)
        linkedin = _map(svc, "LinkedIn", title=title)
        expected = f"{title} (79463)"
        assert zip_job["title"] == expected
        assert indeed["title"] == expected
        assert "Café" in linkedin["title"]
        assert "–" in linkedin["title"]
        assert "/" not in linkedin["title"]
        assert linkedin["title"].endswith("(79463)")
        assert zip_job["url"].split("?")[0] == indeed["url"].split("?")[0] == linkedin["url"].split("?")[0]
        assert zip_job["bhatsid"] == "79463"

    def test_description_lists_survive_cleanup(self):
        mixin = MappingMixin()
        mixin.logger = logging.getLogger("test_board_job_titles")
        cleaned = mixin._clean_description(DESCRIPTION)
        assert "<li>Pick customer orders</li>" in cleaned
        assert "<li>Verify products, quantities, and item numbers</li>" in cleaned
        assert cleaned.count("<li>") == cleaned.count("</li>")
        assert "What You'll Do" in cleaned


class TestMailboxJobId:
    def test_single_suffix_still_matches(self):
        parser = _Parser()
        subject = "Great Match: Jane Candidate for 'Material Handler (46939)'"
        assert parser.extract_bullhorn_job_id(subject, "") == 46939

    def test_appended_id_wins_over_an_earlier_number(self):
        parser = _Parser()
        title = zip_display_title("Role (2024)", JOB_ID)
        subject = f"Great Match: Jane Candidate for '{title}'"
        assert parser.extract_bullhorn_job_id(subject, "") == JOB_ID

    def test_zip_title_with_slashes_still_matches(self):
        parser = _Parser()
        subject = (
            "Great Match: Jane Candidate for "
            f"'{zip_display_title(CANONICAL, JOB_ID)}'"
        )
        assert parser.extract_bullhorn_job_id(subject, "") == JOB_ID

    def test_body_bullhorn_id_when_subject_has_no_suffix(self):
        parser = _Parser()
        assert parser.extract_bullhorn_job_id(
            f"Great Match: Jane Candidate for '{CANONICAL}'",
            "Bullhorn ID: 79463",
        ) == JOB_ID

    def test_dice_job_id_line_is_unchanged(self):
        parser = _Parser()
        subject = "Job ID - 33633 | UX Designer - Moises Frausto has applied"
        assert parser.extract_bullhorn_job_id(subject, "") == 33633

    def test_dice_id_wins_over_a_later_parenthetical(self):
        parser = _Parser()
        subject = "Job ID - 33633 | UX Designer (79463) - Moises Frausto has applied"
        assert parser.extract_bullhorn_job_id(subject, "") == 33633

    def test_linkedin_and_indeed_ignore_a_number_on_the_candidate(self):
        parser = _Parser()
        linkedin = "General Labor / Sorter / Order Picker (79463) - Ana (2024)"
        indeed = "Job Title - Azure (34707) - Candidate Name"
        classic = "Job Title (34613) - Candidate Name"
        assert parser.extract_bullhorn_job_id(linkedin, "") == 79463
        assert parser.extract_bullhorn_job_id(indeed, "") == 34707
        assert parser.extract_bullhorn_job_id(classic, "") == 34613

    def test_unquoted_title_with_two_ids_uses_the_suffix_not_the_name(self):
        parser = _Parser()
        subject = "Role (2024) (79463) - Ana (11111)"
        assert parser.extract_bullhorn_job_id(subject, "") == 79463

    def test_quoted_title_ignores_a_number_on_the_name(self):
        parser = _Parser()
        subject = "Great Match: Ana (2024) for 'Role (2024) (79463)'"
        assert parser.extract_bullhorn_job_id(subject, "") == 79463

    def test_quoted_title_without_an_id_does_not_use_the_name(self):
        parser = _Parser()
        subject = "Great Match: Ana (2024) for 'Material Handler'"
        assert parser.extract_bullhorn_job_id(subject, "") is None

    def test_missing_malformed_and_bare_numbers(self):
        parser = _Parser()
        assert parser.extract_bullhorn_job_id(None, None) is None
        assert parser.extract_bullhorn_job_id("", "") is None
        assert parser.extract_bullhorn_job_id("Role (123)", "") is None
        assert parser.extract_bullhorn_job_id("Role (1234567)", "") is None
        assert parser.extract_bullhorn_job_id("Role (abcd)", "") is None
        assert parser.extract_bullhorn_job_id("year 2024 only", "") is None
        assert parser.extract_bullhorn_job_id("Please review 11111 before 22222", "") == 11111
        assert parser.extract_bullhorn_job_id("Role (46939)", "Bullhorn ID: 11111") == 46939

    def test_title_dash_without_a_candidate_segment_still_matches(self):
        parser = _Parser()
        assert parser.extract_bullhorn_job_id("Job Title - Azure (34707)", "") == 34707
        assert parser.extract_bullhorn_job_id("Job Title - Azure (347071)", "") == 347071
        assert parser.extract_bullhorn_job_id("Role (2024) (79463)", "") == 79463

    def test_apostrophes_and_quotes_do_not_select_the_candidate_number(self):
        parser = _Parser()
        original = """O'Brien / "Night" Sorter"""
        titled = zip_display_title(original, JOB_ID)
        assert titled == f"""{original} ({JOB_ID})"""
        cases = [
            f"Great Match: Ana (11111) for '{titled}'",
            f"""New candidate: D'Angelo (22222) for "{titled}\"""",
            f"Great Match: O'Brien (11111) for '{titled}'",
            f"{titled} - Ana (11111)",
            f"""It's a Sorter (79463) - D'Angelo (22222)""",
        ]
        for subject in cases:
            assert parser.extract_bullhorn_job_id(subject, "") == JOB_ID, subject

    def test_en_dash_separator_still_keeps_the_title_id(self):
        parser = _Parser()
        subject = "Job Title (34613) – Ana (2024)"
        assert parser.extract_bullhorn_job_id(subject, "") == 34613


class TestIndeedNativePublishDoesNotRewriteTitle:
    def test_cfc_payload_has_job_id_and_no_title_field(self):
        import inspect
        from indeed_publish.ui_client import BullhornUIClient

        source = inspect.getsource(BullhornUIClient._post_cfc)
        assert "jobPostingID" in source
        assert "'title'" not in source
        assert '"title"' not in source
