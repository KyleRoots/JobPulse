"""Pure helpers for extracting North American (NANP) phone numbers from text.

extract_nanp_phone() returns the ten ASCII national digits of the first
complete NANP number found in text, or None.

Deliberate limits:
- Only NANP is supported. Area code and exchange must start with 2-9; the
  only accepted country code is a leading '1' or '+1'. Any other '+<code>'
  and 00/011 international prefixes reject the whole token.
- Non-CR/LF whitespace and Unicode dash punctuation/minus are normalized
  to spaces/hyphens before tokenization, so invalid numbers stay whole.
- A token is a run of ASCII digits, '+', spaces, tabs, no-break spaces,
  dots, hyphens and parentheses; '(' is part of a token only when a digit
  or '+' follows it. A single LF/CRLF may join lines inside a token; a blank line
  always ends it. Separate numbers must be divided by something else
  (blank line, comma, semicolon, slash, words). Numbers joined only by
  spaces or hyphens form one oversized token and are rejected rather than
  split. A wrapped token that is not one number is accepted only when
  every line is itself a complete number, so '2025550123\n3035550145'
  yields the first while '+44\n2025550123' is rejected.
  A line longer than the maximum token length is discarded whole before
  considering complete contacts on subsequent lines.
  A complete phone ends before a new line containing a numeric prose field
  (e.g. '123 Main Street') or an ISO date. Bare excess digits remain attached.
- Tokens are validated whole: longer digit runs are never sliced, missing
  digits are never filled in, malformed parentheses are not repaired, and
  a token glued to letters or digits is rejected. A ')' closing a prose
  '(' opened earlier on the same line, as in '(call 202-555-0123)', ends
  the token; inside such an enclosure a letter may precede the number.
- At most one separator (or one line break) is allowed between digit
  groups, so '202  555  0123' or '202 - 555 - 0123' is rejected.
- An extension ('ext', 'extension', 'x', '#', 1-6 digits, optionally in
  parentheses) directly after the number is dropped; a longer or
  incomplete extension rejects the number. A bare 'x<digits>' after a
  number is always read as an extension.
"""
import re
import unicodedata
from typing import Optional

_MAX_TOKEN_LEN = 40
_MAX_EXT_DIGITS = 6
_HSPACE = ' \t\u00a0'

# Run of phone-like characters; ASCII digits only. '(' joins only before a
# digit or '+' and a single LF/CRLF only before more phone syntax, so prose
# parentheses and blank lines end the run. Alternatives are disjoint, so
# matching stays linear.
_RUN_RE = re.compile(
    r'(?:[0-9+]|\((?=[0-9+]))'
    r'(?:[0-9+).\- \t\u00a0]|\((?=[0-9+])|\r?\n[ \t]*(?=[0-9+]|\([0-9+]))*'
)

_SEP = r'(?:[ \t\u00a0.\-]|\r?\n[ \t]*)?'
_NUMERIC_FIELD_RE = re.compile(
    r'[ \t]*(?:[0-9]+[ \t]+[A-Za-z]|'
    r'[0-9]{4}-[0-9]{2}-[0-9]{2}(?=[ \t\r\n]|$))'
)
_NANP_RE = re.compile(
    r'(?:\+?1' + _SEP + r')?' +
    r'(?:\((?P<area_paren>[2-9][0-9]{2})\)|(?P<area>[2-9][0-9]{2}))' + _SEP +
    r'(?P<exchange>[2-9][0-9]{2})' + _SEP +
    r'(?P<line>[0-9]{4})'
)

_EXT_RE = re.compile(
    r'[ \t\u00a0]*(?:extension|ext|x|#)[.:]?[ \t\u00a0]*'
    r'(?:\(([0-9]+)\)|([0-9]+))',
    re.IGNORECASE,
)

_EXT_MARKER_RE = re.compile(
    r'[ \t\u00a0]*(?:extension|ext|x|#)(?=$|[ \t\u00a0.:0-9(])',
    re.IGNORECASE,
)

def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == '_'


def _national_digits(token: str) -> Optional[str]:
    if len(token) > _MAX_TOKEN_LEN:
        return None
    match = _NANP_RE.fullmatch(token)
    if match is None and token.startswith('(') and token.endswith(')'):
        # Whole number wrapped in prose parentheses: '(2025550123)'.
        match = _NANP_RE.fullmatch(token[1:-1])
    if match is None:
        return None
    area = match.group('area_paren') or match.group('area')
    return area + match.group('exchange') + match.group('line')


def _token_digits(token: str) -> Optional[str]:
    phone = _national_digits(token)
    if phone or '\n' not in token:
        return phone
    # Not one wrapped number: accept only separate complete numbers, one per
    # line, so an unsupported prefix or extra digits never yield a substring.
    lines = [line.strip(_HSPACE) for line in re.split(r'\r?\n', token)]
    # A physically oversized line cannot be a supported wrapped phone.
    # Discard that entire line, never a prefix within it. Short invalid
    # lines (notably country prefixes and excess digits) stay attached.
    first = 0
    while first < len(lines) and len(lines[first]) > _MAX_TOKEN_LEN:
        first += 1
    phones = [_national_digits(line) for line in lines[first:]]
    if phones and all(phones):
        return phones[0]
    return None


def _in_prose_parens(text: str, start: int) -> bool:
    # True when an earlier '(' on the same line is still open at start.
    line = text[text.rfind('\n', 0, start) + 1:start]
    opening = line.rfind('(')
    return opening > line.rfind(')') and any(ch.isalpha() for ch in line[opening + 1:])


def _validated_token(text: str, start: int, token: str, scan_end: int):
    """Validate one candidate with identical context rules at every boundary.

    Return (phone, consumed_end), including invalid extensions in consumed_end
    so the scanner cannot recover their digits as a separate phone.
    """
    if start > 0 and text[start - 1] == '(':
        return None, scan_end
    if scan_end < len(text) and text[scan_end] == '(':
        if scan_end + 1 == len(text) or not text[scan_end + 1].isalpha():
            return None, scan_end
    token = token.rstrip(_HSPACE + '\r\n')
    if token.endswith('.'):
        token = token[:-1]
    enclosed = False
    if token.endswith(')') and token.count(')') > token.count('('):
        if not _in_prose_parens(text, start):
            return None, scan_end
        token = token[:-1]
        enclosed = True
    if not token:
        return None, scan_end
    end = start + len(token)
    tail = end
    ext = _EXT_RE.match(text, end)
    if not ext and _EXT_MARKER_RE.match(text, end):
        return None, scan_end
    if ext:
        tail = ext.end()
        scan_end = max(scan_end, tail)
        if len(ext.group(1) or ext.group(2)) > _MAX_EXT_DIGITS:
            return None, scan_end
    if not enclosed and start > 0 and _is_word_char(text[start - 1]):
        return None, scan_end
    if tail < len(text) and _is_word_char(text[tail]):
        return None, scan_end
    return _token_digits(token), scan_end


def extract_nanp_phone(text: Optional[str]) -> Optional[str]:
    """Return the ten national digits of the first complete NANP number in text."""
    if not isinstance(text, str) or not text:
        return None
    # Keep Unicode spacing/dashes inside the same candidate: they must not
    # turn an unsupported prefix or extra digit into a separate token.
    text = ''.join(
        ' ' if ch.isspace() and ch not in '\r\n' else
        '-' if unicodedata.category(ch) == 'Pd' or ch == '\u2212' else ch
        for ch in text
    )
    pos = 0
    while True:
        run = _RUN_RE.search(text, pos)
        if run is None:
            return None
        start = run.start()
        token = run.group(0)
        pos = run.end()
        # Only a recognized independent field can end a numeric run early.
        # Prefixes use the same punctuation, enclosure, extension and context
        # validation as the complete run; neither path repairs invalid syntax.
        for boundary in re.finditer(r'\r?\n', token[:_MAX_TOKEN_LEN + 2]):
            if _NUMERIC_FIELD_RE.match(text, start + boundary.end()):
                phone, _ = _validated_token(
                    text, start, token[:boundary.start()], start + boundary.start())
                if phone:
                    return phone
        phone, pos = _validated_token(text, start, token, pos)
        if phone:
            return phone
