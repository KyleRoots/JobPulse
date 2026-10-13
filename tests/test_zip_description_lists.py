"""Zip list closers for Bullhorn job 79518, reference 69NLEAM5SD.

The fixture is the public description from the Raleigh Medical Office
Assistant listing. Zip must keep that list structure through cleaning and
CDATA. LinkedIn and Indeed keep the historical cleaner.
"""
import hashlib
import logging
import re

from lxml import etree

from xml_integration_service import XMLIntegrationService
from xml_integration_service.mapping import MappingMixin, prepare_description_markup

JOB_ID = 79518
REFERENCE = "69NLEAM5SD"
TITLE = "Medical Office Assistant – Front Desk & Clinical Support"
# Historical cleaner output for this fixture. Locks non-Zip behavior.
HISTORICAL_SHA256 = "afe11dbef3998e100b56e83823feda958287595fbf5b67327ae3566275e4891e"

BULLHORN_DESCRIPTION = """<h1>Medical Office Assistant – Front Desk &amp; Clinical Support</h1>

<p><strong>Raleigh, NC | Full-Time | Monday–Friday | Day Shift</strong></p>

<p><strong>Qualified Staffing is recruiting experienced Medical Office Assistants, Medical Receptionists, and Medical Assistants for current and upcoming opportunities with healthcare practices in the Raleigh area!</strong></p>

<p>We are looking for friendly, dependable, and detail-oriented individuals who enjoy working with patients and supporting busy medical offices.</p>

<p>Opportunities may include front desk, administrative, patient coordination, and clinical support responsibilities depending on experience and the position.</p>

<h3>Responsibilities May Include</h3>

<ul>
	<li>
	<p>Greet patients and provide excellent customer service.</p>
	</li>
	<li>
	<p>Answer phones, schedule appointments, and manage patient inquiries.</p>
	</li>
	<li>
	<p>Verify insurance information and collect copayments.</p>
	</li>
	<li>
	<p>Maintain accurate patient records and update electronic medical records (EMR).</p>
	</li>
	<li>
	<p>Assist with patient check-in and check-out.</p>
	</li>
	<li>
	<p>Coordinate referrals, medical records, and provider schedules.</p>
	</li>
	<li>
	<p>Support clinical staff with patient intake, vital signs, and exam room preparation when qualified.</p>
	</li>
	<li>
	<p>Maintain patient confidentiality and follow HIPAA guidelines.</p>
	</li>
</ul>

<h3>Qualifications</h3>

<ul>
	<li>
	<p>Previous medical office, medical receptionist, or medical assisting experience preferred.</p>
	</li>
	<li>
	<p>Familiarity with electronic medical records (EMR/EHR) systems.</p>
	</li>
	<li>
	<p>Strong computer, communication, and organizational skills.</p>
	</li>
	<li>
	<p>Experience with patient scheduling, insurance verification, or medical terminology is a plus.</p>
	</li>
	<li>
	<p>Medical Assistant certification is a plus for clinical opportunities.</p>
	</li>
	<li>
	<p>Professional, dependable, and comfortable working in a fast-paced healthcare environment.</p>
	</li>
</ul>

<h3>Why Apply?</h3>

<ul>
	<li>
	<p>Full-time, daytime opportunities</p>
	</li>
	<li>
	<p>Monday–Friday schedules for many positions</p>
	</li>
	<li>
	<p>Administrative and clinical opportunities based on your experience</p>
	</li>
	<li>
	<p>Opportunities with established healthcare practices in the Raleigh area</p>
	</li>
	<li>
	<p>Compensation varies based on experience and position</p>
	</li>
</ul>

<p><strong>Apply today!</strong> Our recruiting team would love to learn more about your medical office experience and discuss opportunities that match your background and career goals.</p>
"""

_STRUCTURAL_TAGS = {"h1", "h3", "p", "ul", "ol", "li", "strong"}


def _tag_events(markup: str):
    events = []
    for match in re.finditer(r"</?([a-zA-Z0-9]+)\b[^>]*>", markup):
        name = match.group(1).lower()
        if name not in _STRUCTURAL_TAGS:
            continue
        kind = "close" if match.group(0).startswith("</") else "open"
        events.append((kind, name))
    return events


def _explicit_list_item_texts(markup: str):
    """Item text in close order, using only explicit </li> closers."""
    import html as html_module

    parts = re.split(r"(</?li\b[^>]*>)", markup, flags=re.IGNORECASE)
    texts = []
    stack = []
    for part in parts:
        if re.fullmatch(r"<li\b[^>]*>", part or "", flags=re.IGNORECASE):
            stack.append([])
            continue
        if re.fullmatch(r"</li>", part or "", flags=re.IGNORECASE):
            if not stack:
                continue
            inner = "".join(stack.pop())
            plain = re.sub(r"<[^>]+>", " ", inner)
            texts.append(" ".join(html_module.unescape(plain).split()))
            if stack:
                stack[-1].append(inner)
            continue
        if stack and part:
            stack[-1].append(part)
    return texts


def _paragraph_texts(markup: str):
    import html as html_module

    texts = []
    for inner in re.findall(r"<p\b[^>]*>(.*?)</p>", markup, flags=re.IGNORECASE | re.DOTALL):
        plain = re.sub(r"<[^>]+>", " ", inner)
        texts.append(" ".join(html_module.unescape(plain).split()))
    return texts


def _heading_texts(markup: str):
    import html as html_module

    texts = []
    for inner in re.findall(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", markup, flags=re.IGNORECASE | re.DOTALL):
        plain = re.sub(r"<[^>]+>", " ", inner)
        texts.append(" ".join(html_module.unescape(plain).split()))
    return texts


def _cdata_description(value: str) -> str:
    """Same CDATA wrap the feed generator writes around every field."""
    root = etree.Element("job")
    node = etree.SubElement(root, "description")
    node.text = etree.CDATA(f" {value} ")
    parsed = etree.fromstring(etree.tostring(root, xml_declaration=True, encoding="UTF-8"))
    return parsed.find("description").text.strip()


def _mixin():
    mixin = MappingMixin()
    mixin.logger = logging.getLogger("test_zip_description_lists")
    return mixin


def _map(monkeypatch, tenant, channel, description=None):
    if tenant:
        monkeypatch.setenv("SCOUT_TENANT", tenant)
    else:
        monkeypatch.delenv("SCOUT_TENANT", raising=False)
    service = XMLIntegrationService()
    return service.map_bullhorn_job_to_xml(
        {
            "id": JOB_ID,
            "title": TITLE,
            "publicDescription": BULLHORN_DESCRIPTION if description is None else description,
            "address": {
                "city": "Raleigh",
                "state": "North Carolina",
                "countryName": "United States",
            },
            "employmentType": "Full Time",
        },
        existing_reference_number=REFERENCE,
        skip_ai_classification=True,
        existing_ai_fields={
            "jobfunction": "Healthcare",
            "jobindustries": "Medical",
            "senioritylevel": "Entry level",
        },
        source_channel=channel,
    )


class TestZipListClosers:
    def test_first_pass_and_parser_keep_the_bullhorn_structure(self):
        source_events = _tag_events(BULLHORN_DESCRIPTION)
        source_items = _explicit_list_item_texts(BULLHORN_DESCRIPTION)
        assert len(source_items) == 19
        assert source_items[0] == "Greet patients and provide excellent customer service."
        assert source_items[-1] == "Compensation varies based on experience and position"

        prepared = prepare_description_markup(
            BULLHORN_DESCRIPTION,
            preserve_explicit_list_closers=True,
        )
        assert prepared.count("</li>") == 19
        assert _tag_events(prepared) == source_events
        assert _explicit_list_item_texts(prepared) == source_items
        assert _heading_texts(prepared) == _heading_texts(BULLHORN_DESCRIPTION)
        assert _paragraph_texts(prepared) == _paragraph_texts(BULLHORN_DESCRIPTION)

        cleaned = _mixin()._clean_description(
            BULLHORN_DESCRIPTION,
            source_channel="ZipRecruiter",
        )
        assert _tag_events(cleaned) == source_events
        assert _explicit_list_item_texts(cleaned) == source_items
        assert _heading_texts(cleaned) == [
            "Medical Office Assistant – Front Desk & Clinical Support",
            "Responsibilities May Include",
            "Qualifications",
            "Why Apply?",
        ]
        assert "&amp;amp;" not in cleaned
        assert cleaned.count("<li>") == cleaned.count("</li>") == 19
        assert cleaned.count("<ul>") == cleaned.count("</ul>") == 3

    def test_final_xml_round_trip_keeps_wording_and_order(self):
        cleaned = _mixin()._clean_description(
            BULLHORN_DESCRIPTION,
            source_channel="ZipRecruiter",
        )
        decoded = _cdata_description(cleaned)
        assert _explicit_list_item_texts(decoded) == _explicit_list_item_texts(BULLHORN_DESCRIPTION)
        assert _paragraph_texts(decoded) == _paragraph_texts(BULLHORN_DESCRIPTION)
        assert _heading_texts(decoded) == _heading_texts(BULLHORN_DESCRIPTION)
        assert _tag_events(decoded) == _tag_events(BULLHORN_DESCRIPTION)

    def test_repeated_cleaning_does_not_drop_closers(self):
        mixin = _mixin()
        once = mixin._clean_description(BULLHORN_DESCRIPTION, source_channel="ZipRecruiter")
        twice = mixin._clean_description(once, source_channel="ZipRecruiter")
        assert _tag_events(twice) == _tag_events(once)
        assert _explicit_list_item_texts(twice) == _explicit_list_item_texts(BULLHORN_DESCRIPTION)

    def test_nested_lists_and_line_leading_closers(self):
        nested = (
            "<ul>\n"
            "\t<li>\n"
            "\t<p>Outer item</p>\n"
            "\t<ul>\n"
            "\t\t<li>\n"
            "\t\t<p>Inner item</p>\n"
            "\t\t</li>\n"
            "\t</ul>\n"
            "\t</li>\n"
            "</ul>"
        )
        prepared = prepare_description_markup(nested, preserve_explicit_list_closers=True)
        cleaned = _mixin()._clean_description(nested, source_channel="ZipRecruiter")
        assert _tag_events(prepared) == _tag_events(nested)
        assert _tag_events(cleaned) == _tag_events(nested)
        assert _explicit_list_item_texts(cleaned) == _explicit_list_item_texts(nested)
        assert any("Outer item" in item for item in _explicit_list_item_texts(cleaned))
        assert "Inner item" in _explicit_list_item_texts(cleaned)

    def test_entities_stay_single_escaped(self):
        source = "<p>A &amp; B &#39;ok&#39;</p>"
        mixin = _mixin()
        cleaned = mixin._clean_description(source, source_channel="ZipRecruiter")
        historical = mixin._clean_description(source)
        decoded = _cdata_description(cleaned)
        assert "&amp;amp;" not in decoded
        assert _paragraph_texts(decoded) == ["A & B 'ok'"]
        # The HTML repair parse treats &lt;...&gt; as markup. Zip must not
        # change that existing result.
        tagged = "<p>A &amp; B &lt;not a tag&gt;</p>"
        assert mixin._clean_description(tagged, source_channel="ZipRecruiter") == mixin._clean_description(tagged)
        assert cleaned == historical

    def test_orphan_closer_bare_list_and_unclosed_item_still_sanitized(self):
        mixin = _mixin()
        orphan = "</li>\n<p>Visible</p>\n<ul>\n\t<li>\n\t<p>Kept</p>\n\t</li>\n</ul>"
        prepared = prepare_description_markup(orphan, preserve_explicit_list_closers=True)
        assert not prepared.lstrip().startswith("</li>")
        assert _explicit_list_item_texts(prepared) == ["Kept"]
        historical = prepare_description_markup(orphan, preserve_explicit_list_closers=False)
        assert not historical.lstrip().startswith("</li>")
        bare = mixin._clean_description("<li>Only item</li>", source_channel="ZipRecruiter")
        assert bare.startswith("<ul>")
        assert bare.count("</li>") == 1
        repaired = mixin._clean_description("<ul><li>Open item</ul>", source_channel="ZipRecruiter")
        assert repaired.count("<li>") == repaired.count("</li>") == 1
        assert "Open item" in repaired

    def test_cdata_terminator_stays_inside_a_parseable_description(self):
        source = "<p>keep ]]> split</p>\n<ul>\n\t<li>\n\t<p>Item</p>\n\t</li>\n</ul>"
        cleaned = _mixin()._clean_description(source, source_channel="ZipRecruiter")
        decoded = _cdata_description(cleaned)
        assert _paragraph_texts(decoded)[0] == "keep ]]> split"
        assert _explicit_list_item_texts(decoded) == ["Item"]
        assert "]]>" not in cleaned

    def test_qualified_and_myticas_zip_paths(self, monkeypatch):
        source_items = _explicit_list_item_texts(BULLHORN_DESCRIPTION)
        qualified = _map(monkeypatch, "qualified_staffing", "ZipRecruiter")
        myticas = _map(monkeypatch, "", "ZipRecruiter")
        for mapped, host in (
            (qualified, "https://qualified.scoutgenius.ai/79518/"),
            (myticas, "https://apply.myticas.com/79518/"),
        ):
            assert mapped["bhatsid"] == "79518"
            assert mapped["referencenumber"] == REFERENCE
            assert mapped["title"] == f"{TITLE} (79518)"
            assert mapped["url"].startswith(host)
            assert mapped["url"].endswith("?source=ZipRecruiter")
            decoded = _cdata_description(mapped["description"])
            assert _explicit_list_item_texts(decoded) == source_items
            assert _tag_events(decoded) == _tag_events(BULLHORN_DESCRIPTION)
        assert qualified["url"].split("?")[0] != myticas["url"].split("?")[0]
        assert qualified["url"].split("/")[3:] == myticas["url"].split("/")[3:]

    def test_linkedin_and_indeed_keep_the_historical_description(self, monkeypatch):
        linkedin = _map(monkeypatch, "qualified_staffing", "LinkedIn")
        indeed = _map(monkeypatch, "qualified_staffing", "Indeed")
        assert hashlib.sha256(linkedin["description"].encode()).hexdigest() == HISTORICAL_SHA256
        assert hashlib.sha256(indeed["description"].encode()).hexdigest() == HISTORICAL_SHA256
        assert linkedin["description"].count("</li>") == 0
        assert linkedin["description"].count("<li>") == 19
        assert linkedin["bhatsid"] == indeed["bhatsid"] == "79518"
        assert linkedin["referencenumber"] == indeed["referencenumber"] == REFERENCE
        assert linkedin["url"].split("?")[0] == indeed["url"].split("?")[0]
        assert linkedin["url"].endswith("?source=LinkedIn")
        assert indeed["url"].endswith("?source=Indeed")
        assert "/79518/" in linkedin["url"]
        zip_job = _map(monkeypatch, "qualified_staffing", "ZipRecruiter")
        assert zip_job["url"].split("?")[0] == linkedin["url"].split("?")[0]
        assert linkedin["title"].endswith("(79518)")
        assert indeed["title"] == f"{TITLE} (79518)"
