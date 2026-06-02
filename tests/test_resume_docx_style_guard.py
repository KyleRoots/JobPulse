"""
Regression test for the DOCX style-name null-guard.

Background (Jun 2026): python-docx can return a paragraph whose
`style` object exists but whose `style.name` is None (built-in/unmapped
styles). The old guard only checked `if para.style`, so `para.style.name.lower()`
raised "'NoneType' object has no attribute 'lower'". That exception was
swallowed by the broad except in _extract_docx_with_formatting, causing the
ENTIRE readable .docx to return empty text — surfacing as a candidate with no
resume content.

The fix: `... if para.style and para.style.name else ''`.
"""
from unittest.mock import MagicMock, patch


def _fake_paragraph(text, style_name):
    para = MagicMock()
    para.text = text
    style = MagicMock()
    style.name = style_name
    para.style = style
    para.runs = []
    return para


def test_docx_with_none_style_name_does_not_return_empty():
    """A paragraph whose style.name is None must NOT crash extraction or
    cause the whole document to come back empty."""
    from resume_parser import ResumeParser

    fake_doc = MagicMock()
    fake_doc.sections = []
    fake_doc.tables = []
    fake_doc.paragraphs = [
        _fake_paragraph("John Smith", None),               # style.name is None
        _fake_paragraph("Senior Engineer with 10 years", None),
    ]
    fake_doc.element.findall.return_value = []

    parser = ResumeParser()
    with patch('resume_parser.Document', return_value=fake_doc):
        raw_text, formatted_html = parser._extract_docx_with_formatting('/tmp/whatever.docx')

    assert "John Smith" in raw_text
    assert "Senior Engineer with 10 years" in raw_text
    assert raw_text.strip() != ""
