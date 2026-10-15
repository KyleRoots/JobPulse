"""Synthetic tests for extract_nanp_phone in utils/phone_numbers.py.

The helper is loaded directly from its file so these tests need no app
imports, package __init__ side effects or conftest fixtures. All numbers
are synthetic 555-01xx values.
"""
import importlib.util
from pathlib import Path

import pytest

_HELPER_PATH = Path(__file__).resolve().parents[1] / 'utils' / 'phone_numbers.py'
_spec = importlib.util.spec_from_file_location('_phone_numbers_under_test', _HELPER_PATH)
_phone_numbers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_phone_numbers)
extract_nanp_phone = _phone_numbers.extract_nanp_phone


def test_country_prefixed_eleven_digits_keeps_final_digit():
    # Regression: the old unbounded 3+3+4 regex returned '1202555012'.
    assert extract_nanp_phone('Phone: 12025550123') == '2025550123'
    assert extract_nanp_phone('Phone: +12025550123') == '2025550123'
    assert extract_nanp_phone('12025550123') != '1202555012'


@pytest.mark.parametrize('text, expected', [
    ('+1 (202)555-0123', '2025550123'),
    ('+1 (202) 555-0123', '2025550123'),
    ('1-202-555-0123', '2025550123'),
    ('+1-303-555-0145', '3035550145'),
    ('1.303.555.0145', '3035550145'),
    ('(202)555-0123', '2025550123'),
    ('(202) 555-0123', '2025550123'),
    ('202 555 0123', '2025550123'),
    ('202.555.0123', '2025550123'),
    ('202-555-0123', '2025550123'),
    ('2025550123', '2025550123'),
    ('Mobile:\u00a0303\u00a0555\u00a00145', '3035550145'),
    ('Call 303-555-0145.', '3035550145'),
    ('Contact (2025550123) today', '2025550123'),
    ('Phone\n2025550123\nThanks', '2025550123'),
])
def test_valid_nanp_formats(text, expected):
    assert extract_nanp_phone(text) == expected


def test_result_is_exactly_ten_ascii_digits():
    result = extract_nanp_phone('+1 (303) 555-0145 ext. 7')
    assert result == '3035550145'
    assert len(result) == 10 and result.isascii() and result.isdigit()


@pytest.mark.parametrize('text', [
    '202555012',
    '1 202555012',
    '120255501234',
    '20255501234567',
    '0012025550123',
    '00 1 202 555 0123',
    '0112025550123',
    '011 1 202-555-0123',
    '+44 20 7946 0958',
    '+44 (202)555-0123',
    '+2025550123',
    '+ 202 555 0123',
    '+1',
    'Phone: 1',
    '(202 555-0123',
    '202)555-0123',
    '((202))555-0123',
    'abc2025550123',
    '2025550123abc',
    'ID+12025550123',
    '(102) 555-0123',
    '202-155-0123',
    '\u0662\u0660\u0662\u0665\u0665\u0665\u0660\u0661\u0662\u0663',
    '',
    'no phone here',
])
def test_rejects_without_partial_match(text):
    assert extract_nanp_phone(text) is None


@pytest.mark.parametrize('value', [None, 12025550123, b'2025550123'])
def test_non_text_input_returns_none(value):
    assert extract_nanp_phone(value) is None


@pytest.mark.parametrize('text', [
    '202-555-0123 ext. 45',
    '202-555-0123 extension 4567',
    '(202) 555-0123 x12',
    '2025550123x45',
    '(202) 555-0123 #12',
    '+1 202 555 0123 EXT: 123456',
])
def test_extension_is_excluded(text):
    assert extract_nanp_phone(text) == '2025550123'


@pytest.mark.parametrize('text', [
    '202-555-0123 ext 1234567',
    '2025550123x1234567',
    '(202) 555-0123 #12345678901',
    '202-555-0123 ext45abc',
])
def test_oversized_or_malformed_extension_rejects_number(text):
    assert extract_nanp_phone(text) is None


@pytest.mark.parametrize('text', [
    '2025550123\n3035550145',
    '2025550123, 3035550145',
    '2025550123; 3035550145',
    '2025550123/3035550145',
    'Home (202) 555-0123 or mobile 303-555-0145',
    '+1 202 555 0123 ext 9 / +1 303 555 0145',
])
def test_first_of_multiple_delimited_numbers(text):
    assert extract_nanp_phone(text) == '2025550123'


@pytest.mark.parametrize('text', [
    'Fax: 120255501234\nMobile: 3035550145',
    '202555012, 303-555-0145',
    '+44 20 7946 0958\nMobile: (303) 555-0145',
    '+44 (202)555-0123, 303-555-0145',
    '0112025550123; 3035550145',
    'abc2025550123, 3035550145',
    '(202 555-0123 / 303.555.0145',
    '202-555-0123 ext 1234567; 303-555-0145',
    'Applied 2026-10-14\nPhone: 303-555-0145',
])
def test_invalid_candidate_is_skipped_whole_before_later_valid_number(text):
    assert extract_nanp_phone(text) == '3035550145'


@pytest.mark.parametrize('text', [
    '2025550123 3035550145',
    '2025550123-3035550145',
    'Applied 2026 2025550123',
])
def test_numbers_joined_only_by_spaces_or_hyphens_are_not_split(text):
    # Documented limit: one oversized token is rejected rather than guessed at.
    assert extract_nanp_phone(text) is None


def test_long_phone_like_runs_are_bounded_and_rejected():
    assert extract_nanp_phone('1-' * 20000 + '\n303-555-0145') == '3035550145'
    assert extract_nanp_phone('(' * 20000 + '202) 555-0123') is None

@pytest.mark.parametrize('text', [
    '2025550123+', '2025550123(', '2025550123)', '2025550123-',
    '2025550123 ext', '2025550123 #',
    '2025550123 ext abc',
])
def test_incomplete_phone_syntax_is_not_trimmed_or_repaired(text):
    assert extract_nanp_phone(text) is None

@pytest.mark.parametrize('separator', ['\u202f', '\u2009', '\u2010', '\u2011', '\u2013', '\u2212'])
def test_unicode_separator_does_not_split_invalid_number(separator):
    assert extract_nanp_phone('+44' + separator + '2025550123') is None
    assert extract_nanp_phone('2025550123' + separator + '4') is None
    assert extract_nanp_phone('+1' + separator + '2025550123') == '2025550123'
    assert extract_nanp_phone('+44' + separator + '2025550123; 3035550145') == '3035550145'


@pytest.mark.parametrize('text, expected', [
    ('Phone: 202-555-0123 (mobile)', '2025550123'),
    ('(call 303-555-0145)', '3035550145'),
    ('(call303-555-0145)', '3035550145'),
    ('202-555-0123 ext. (45)', '2025550123'),
    ('202\n555\n0123', '2025550123'),
    ('202\r\n555\r\n0123', '2025550123'),
    ('202\n 555\n 0123', '2025550123'),
    ('(+12025550123)', '2025550123'),
])
def test_prose_extensions_and_line_wraps_preserve_valid_phone(text, expected):
    assert extract_nanp_phone(text) == expected


@pytest.mark.parametrize('text', [
    '((202)555-0123', '(2025550123', '2025550123((',
    '+44\n2025550123', '2025550123\n4',
    '+44\n 2025550123', '+44\r\n\t2025550123', '2025550123\n 4',
    '2025550123 ext. (45', '2025550123 ext. (1234567)',
])
def test_malformed_boundaries_and_wrapped_invalid_tokens(text):
    assert extract_nanp_phone(text) is None


@pytest.mark.parametrize('phone', ['202-555-0123', '+12025550123', '202\n555\n0123'])
@pytest.mark.parametrize('field', ['123 Main Street', '2026-10-15', '2026-10-15 Applied'])
def test_complete_phone_before_numeric_address_or_date(phone, field):
    assert extract_nanp_phone('Phone: ' + phone + '\n' + field +
                              '\nOffice: 303-555-0145') == '2025550123'


@pytest.mark.parametrize('text', ['+44\n2025550123\n123 Main Street',
                                '2025550123\n45', '2025550123\n 45',
                                '2025550123 123 Main Street'])
def test_numeric_field_boundary_does_not_split_invalid_tokens(text):
    assert extract_nanp_phone(text) is None


@pytest.mark.parametrize('phone', [
    '202-555-0123.', '(call 202-555-0123)', '(call 202-555-0123).',
    '(202) 555-0123', '(2025550123)', '+12025550123',
    '202-555-0123 ext. 45', '202-555-0123 ext. (45)',
    '202\n555\n0123', '202\n555\n0123.',
])
@pytest.mark.parametrize('field', ['123 Main Street', '2026-10-15'])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_composed_phone_formats_and_independent_numeric_fields(phone, field, newline):
    text = 'Phone: ' + phone.replace('\n', newline) + newline + field
    assert extract_nanp_phone(text) == '2025550123'
    assert extract_nanp_phone(text + newline + 'Office: 303-555-0145') == '2025550123'


@pytest.mark.parametrize('phone', [
    '((202)555-0123', '2025550123(', '+44\n2025550123',
    '120255501234', '2025550123\n4', '2025550123 ext. (1234567)',
])
def test_invalid_composed_prefix_cannot_hide_before_address(phone):
    assert extract_nanp_phone('Phone: ' + phone + '\n123 Main Street') is None
