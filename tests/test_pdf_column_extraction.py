"""
Regression tests for column-aware PDF résumé extraction.

Background: ``page.get_text("text")`` follows the PDF's internal token order,
which on two-column résumés interleaves the left (sidebar) and right (body)
columns — shredding skills/contact sidebars into the work history. The
``ResumeParser._extract_pdf_page_text`` helper reorders confidently-detected
two-column pages column-by-column, while leaving everything else (single-column
résumés, including those with right-aligned dates) on the original
``get_text("text")`` path so they are byte-for-byte unchanged.

These tests exercise the classifier with a fake page object so no real PDF or
PyMuPDF runtime is required.
"""


class _FakeRect:
    def __init__(self, x0, width):
        self.x0 = x0
        self.width = width


class _FakePage:
    """Mimics the slice of the PyMuPDF Page API the helper touches."""

    def __init__(self, blocks, rect, text_sentinel):
        self._blocks = blocks
        self.rect = rect
        self._text = text_sentinel

    def get_text(self, mode):
        if mode == "blocks":
            return self._blocks
        return self._text


def _parser():
    from resume_parser import ResumeParser
    return ResumeParser()


def _block(x0, y0, x1, y1, text):
    # PyMuPDF block tuple: (x0, y0, x1, y1, "text", block_no, block_type)
    return (x0, y0, x1, y1, text, 0, 0)


def test_single_column_returns_default_text_unchanged():
    """A plain single-column page must fall back to get_text('text') verbatim."""
    page_width = 600.0
    blocks = [
        _block(50, 50, 550, 70, "JANE DOE — Senior Engineer"),
        _block(50, 90, 550, 140, "Experienced backend engineer with a long history."),
        _block(50, 160, 550, 220, "Worked across distributed systems and data."),
        _block(50, 240, 550, 300, "Led teams and shipped reliable services."),
        _block(50, 320, 550, 380, "Education and certifications follow below."),
        _block(50, 400, 550, 460, "References available upon request."),
    ]
    page = _FakePage(blocks, _FakeRect(0, page_width), "SENTINEL-DEFAULT-TEXT")
    out = _parser()._extract_pdf_page_text(page)
    assert out == "SENTINEL-DEFAULT-TEXT"


def test_single_column_with_right_aligned_dates_not_reordered():
    """The tricky regression case: a single column whose entries carry small
    right-aligned date stamps must NOT be mistaken for a two-column layout."""
    page_width = 600.0
    blocks = [
        _block(50, 50, 520, 70, "JANE DOE"),
        # Wide body entries (cross the centre gutter) + tiny right-aligned dates.
        _block(50, 100, 480, 150, "Senior Engineer at Acme — built core platform services."),
        _block(500, 100, 560, 116, "2019–2023"),
        _block(50, 170, 480, 220, "Engineer at Beta — owned the ingestion pipeline."),
        _block(500, 170, 560, 186, "2016–2019"),
        _block(50, 240, 480, 290, "Junior Dev at Gamma — internal tooling and tests."),
        _block(500, 240, 560, 256, "2014–2016"),
    ]
    page = _FakePage(blocks, _FakeRect(0, page_width), "SENTINEL-SINGLE-COL")
    out = _parser()._extract_pdf_page_text(page)
    # Must fall back to default text (no reordering of the date stamps).
    assert out == "SENTINEL-SINGLE-COL"


def test_single_column_with_verbose_right_dates_not_reordered():
    """Adversarial: right-aligned stamps that are wordier (e.g. 'Jan 2019 –
    Present, Chicago, IL') still represent a minority of the text mass, so the
    20% column-mass cutoff must keep the page on the default path."""
    page_width = 600.0
    long_body = (
        "Senior Staff Engineer at Acme Corporation — designed, built and "
        "operated the company's core data ingestion and screening platform, "
        "mentoring a team of eight across three time zones."
    )
    blocks = [
        _block(50, 50, 520, 70, "JANE DOE"),
        _block(50, 100, 470, 170, long_body),
        _block(490, 100, 565, 132, "Jan 2019 – Present, Chicago, IL"),
        _block(50, 190, 470, 260, long_body),
        _block(490, 190, 565, 222, "Mar 2016 – Dec 2018, Austin, TX"),
        _block(50, 280, 470, 350, long_body),
        _block(490, 280, 565, 312, "Jun 2014 – Feb 2016, Remote, USA"),
    ]
    page = _FakePage(blocks, _FakeRect(0, page_width), "SENTINEL-VERBOSE-DATES")
    out = _parser()._extract_pdf_page_text(page)
    assert out == "SENTINEL-VERBOSE-DATES"


def test_true_two_column_reads_left_then_right():
    """A genuine two-column résumé (substantial sidebar + body) must be read
    left column top-to-bottom, then right column top-to-bottom, losslessly."""
    page_width = 600.0
    blocks = [
        # Full-width header banner (above both columns).
        _block(40, 30, 560, 60, "JANE DOE — SENIOR ENGINEER"),
        # Left sidebar column (substantial text mass).
        _block(40, 90, 250, 140, "CONTACT: jane@example.com / 555-0100 / Chicago, IL"),
        _block(40, 160, 250, 230, "SKILLS: Python, Go, PostgreSQL, distributed systems, AWS"),
        _block(40, 250, 250, 330, "EDUCATION: BSc Computer Science, State University, 2014"),
        # Right body column (substantial text mass).
        _block(330, 90, 560, 150, "EXPERIENCE: Senior Engineer at Acme building platform."),
        _block(330, 170, 560, 240, "Engineer at Beta owning the ingestion and data layer."),
        _block(330, 260, 560, 330, "Junior Developer at Gamma writing internal tooling."),
    ]
    page = _FakePage(blocks, _FakeRect(0, page_width), "SHOULD-NOT-BE-USED")
    out = _parser()._extract_pdf_page_text(page)

    assert out != "SHOULD-NOT-BE-USED"  # reordering kicked in
    lines = out.split("\n")
    # Header leads.
    assert lines[0].startswith("JANE DOE")
    # All sidebar (left) content precedes all body (right) content.
    left_marker = lines.index(next(l for l in lines if l.startswith("CONTACT")))
    right_marker = lines.index(next(l for l in lines if l.startswith("EXPERIENCE")))
    assert left_marker < right_marker
    # Lossless: every block's text survives.
    for marker in ("CONTACT", "SKILLS", "EDUCATION", "EXPERIENCE", "Junior Developer"):
        assert any(marker in l for l in lines), marker


def test_two_column_header_leads_and_footer_span_trails():
    """A two-column page with BOTH a top header banner and a lower full-width
    footer must lead with the header, read both columns, then place the footer
    last — never letting the mid/footer span jump above body content."""
    page_width = 600.0
    blocks = [
        _block(40, 30, 560, 60, "JANE DOE — HEADER BANNER"),
        _block(40, 90, 250, 150, "CONTACT sidebar with a real amount of text here."),
        _block(40, 170, 250, 240, "SKILLS sidebar Python Go SQL distributed systems."),
        _block(40, 260, 250, 330, "EDUCATION sidebar BSc Computer Science 2014 honors."),
        _block(330, 90, 560, 150, "EXPERIENCE body Senior Engineer at Acme platform."),
        _block(330, 170, 560, 240, "BODY Engineer at Beta ingestion and data layer work."),
        _block(330, 260, 560, 330, "BODY Junior Developer at Gamma internal tooling work."),
        # Full-width footer below both columns.
        _block(40, 400, 560, 430, "FOOTER references available upon request banner."),
    ]
    page = _FakePage(blocks, _FakeRect(0, page_width), "SHOULD-NOT-BE-USED")
    out = _parser()._extract_pdf_page_text(page)
    lines = out.split("\n")
    assert lines[0].startswith("JANE DOE")          # header leads
    assert lines[-1].startswith("FOOTER")           # footer trails
    # header < all columns < footer
    assert lines.index(next(l for l in lines if l.startswith("CONTACT"))) < \
        lines.index(next(l for l in lines if l.startswith("EXPERIENCE")))
    assert lines.index(next(l for l in lines if l.startswith("EXPERIENCE"))) < \
        lines.index(next(l for l in lines if l.startswith("FOOTER")))
