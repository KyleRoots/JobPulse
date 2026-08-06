"""Conservative country inference for candidate records.

This module is deliberately deterministic. Country writes affect Bullhorn
native search, so the automation only acts when the candidate's city/state is
corroborated by the parsed resume and maps unambiguously to one country. It
does not treat citizenship, passport, employer, or school references as proof
of current residence.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re
from typing import Dict, Iterable, Optional

from vetting.geo_utils import normalize_country


@dataclass(frozen=True)
class CountryDefinition:
    name: str
    code: str
    bullhorn_id: int


@dataclass(frozen=True)
class CountryResolution:
    country: CountryDefinition
    confidence: str
    evidence: str


# Bullhorn Country entity IDs are tenant-stable. Values were verified against
# live Candidate records on Jul 30 2026. Dynamic /options/Country values may
# override these in the normalizer service when available.
COUNTRIES: Dict[str, CountryDefinition] = {
    "United States": CountryDefinition("United States", "US", 1),
    "Canada": CountryDefinition("Canada", "CA", 2216),
    "United Kingdom": CountryDefinition("United Kingdom", "UK", 2359),
    "Australia": CountryDefinition("Australia", "AU", 2194),
    "Mexico": CountryDefinition("Mexico", "MX", 2296),
    "Egypt": CountryDefinition("Egypt", "EG", 2237),
    "India": CountryDefinition("India", "IN", 2262),
    "Pakistan": CountryDefinition("Pakistan", "PK", 2313),
    "Philippines": CountryDefinition("Philippines", "PH", 2319),
}

COUNTRY_ALIASES = {
    "USA": "United States",
    "US": "United States",
    "UNITED STATES OF AMERICA": "United States",
    "CAN": "Canada",
    "CDN": "Canada",
    "UK": "United Kingdom",
    "GB": "United Kingdom",
    "GREAT BRITAIN": "United Kingdom",
}

CANADIAN_REGIONS = {
    "AB", "BC", "MB", "NB", "NL", "NS", "NU", "ON", "PE", "QC", "SK", "YT",
    "ALBERTA", "BRITISH COLUMBIA", "MANITOBA", "NEW BRUNSWICK",
    "NEWFOUNDLAND", "NEWFOUNDLAND AND LABRADOR", "NOVA SCOTIA", "NUNAVUT",
    "ONTARIO", "PRINCE EDWARD ISLAND", "QUEBEC", "SASKATCHEWAN", "YUKON",
}
US_REGIONS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}
UK_REGIONS = {
    "ENGLAND", "SCOTLAND", "WALES", "NORTHERN IRELAND", "GREATER LONDON",
}
AUSTRALIAN_REGIONS = {
    "NEW SOUTH WALES", "VICTORIA", "QUEENSLAND", "WESTERN AUSTRALIA",
    "SOUTH AUSTRALIA", "TASMANIA", "AUSTRALIAN CAPITAL TERRITORY",
}
MEXICAN_REGIONS = {
    "JALISCO", "NUEVO LEON", "QUINTANA ROO", "BAJA CALIFORNIA", "CDMX",
}
EGYPTIAN_REGIONS = {
    "CAIRO", "ALEXANDRIA", "GIZA", "QALYUBIA", "DAKAHLIA", "LUXOR", "ASWAN",
}
INDIAN_REGIONS = {
    "ANDHRA PRADESH", "ASSAM", "BIHAR", "DELHI", "GUJARAT", "HARYANA",
    "KARNATAKA", "KERALA", "MAHARASHTRA", "PUNJAB", "RAJASTHAN",
    "TAMIL NADU", "TELANGANA", "UTTAR PRADESH", "WEST BENGAL",
}
PAKISTANI_REGIONS = {
    "SINDH", "BALOCHISTAN", "KHYBER PAKHTUNKHWA", "KPK",
    "ISLAMABAD CAPITAL TERRITORY",
}
PHILIPPINE_REGIONS = {
    "NCR", "NATIONAL CAPITAL REGION", "METRO MANILA", "CALABARZON",
    "CENTRAL LUZON", "CENTRAL VISAYAS", "DAVAO REGION",
}

REGION_COUNTRIES = (
    (CANADIAN_REGIONS, "Canada"),
    (US_REGIONS, "United States"),
    (UK_REGIONS, "United Kingdom"),
    (AUSTRALIAN_REGIONS, "Australia"),
    (MEXICAN_REGIONS, "Mexico"),
    (EGYPTIAN_REGIONS, "Egypt"),
    (INDIAN_REGIONS, "India"),
    (PAKISTANI_REGIONS, "Pakistan"),
    (PHILIPPINE_REGIONS, "Philippines"),
)

# Two-letter regions where a write would be unsafe without an explicit country
# on the same resume location line.
AMBIGUOUS_REGION_CODES = {"IN", "WA", "NT", "CA"}
NON_RESIDENCE_TERMS = {
    "CITIZEN", "CITIZENSHIP", "PASSPORT", "NATIONALITY", "AUTHORIZED TO WORK",
}


def country_definition(value: object) -> Optional[CountryDefinition]:
    """Resolve a supported name/code/alias to its Bullhorn country definition."""
    if value is None:
        return None
    if isinstance(value, int) or (isinstance(value, str) and value.strip().isdigit()):
        country_id = int(value)
        return next(
            (
                definition
                for definition in COUNTRIES.values()
                if definition.bullhorn_id == country_id
            ),
            None,
        )
    raw = str(value).strip()
    if not raw:
        return None
    normalized = normalize_country(raw)
    canonical = COUNTRY_ALIASES.get(normalized.upper(), normalized)
    if canonical in COUNTRIES:
        return COUNTRIES[canonical]
    upper = raw.upper()
    for definition in COUNTRIES.values():
        if upper in {definition.name.upper(), definition.code.upper()}:
            return definition
    return None


def bullhorn_country_payload(value: object) -> Dict[str, object]:
    """Return the complete Bullhorn address-country payload for a known country."""
    definition = country_definition(value)
    if not definition:
        return {}
    return {
        "countryID": definition.bullhorn_id,
        "countryName": definition.name,
        "countryCode": definition.code,
    }


def _resume_lines(resume_text: str) -> Iterable[str]:
    text = unescape(re.sub(r"<[^>]+>", "\n", resume_text or ""))
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if line:
            yield line


def _contains_token(line: str, value: str) -> bool:
    value = (value or "").strip()
    if not value:
        return False
    flags = 0 if len(value) == 2 else re.IGNORECASE
    lookup = value.upper() if len(value) == 2 else value
    return bool(re.search(rf"(?<!\w){re.escape(lookup)}(?!\w)", line, flags))


def _explicit_country_on_line(line: str) -> Optional[CountryDefinition]:
    upper = line.upper()
    if any(term in upper for term in NON_RESIDENCE_TERMS):
        return None
    matches = []
    for definition in COUNTRIES.values():
        names = {definition.name.upper()}
        if definition.name == "United States":
            names.update({"USA", "U.S.A.", "UNITED STATES OF AMERICA"})
        elif definition.name == "United Kingdom":
            names.update({"UK", "U.K.", "GREAT BRITAIN"})
        if any(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", upper) for name in names):
            matches.append(definition)
    return matches[0] if len(matches) == 1 else None


def infer_country_from_resume(
    city: object,
    state: object,
    resume_text: object,
) -> Optional[CountryResolution]:
    """Infer residence country from a resume-correlated city/state.

    A result is returned only when a location line in the first 40 non-empty
    resume lines contains the Bullhorn city or state. Citizenship-only text and
    unrelated country mentions do not qualify.
    """
    city_text = str(city or "").strip()
    state_text = str(state or "").strip()
    state_upper = state_text.upper()
    lines = list(_resume_lines(str(resume_text or "")))[:40]
    if not lines:
        return None

    # Populate a blank country even when the board omitted the whole address,
    # but only from an explicit country on a header-style location/contact
    # line. A bare citizenship/passport statement is rejected by
    # _explicit_country_on_line.
    if not (city_text or state_text):
        for line in lines[:12]:
            explicit = _explicit_country_on_line(line)
            upper_line = line.upper()
            looks_like_location = (
                "LOCATION" in upper_line
                or "|" in line
                or "@" in line
                or (
                    "," in line
                    and len(line) <= 120
                    and not any(
                        term in upper_line
                        for term in (
                            "UNIVERSITY", "COLLEGE", "EMPLOYER", "COMPANY",
                            "EXPERIENCE", "EDUCATION",
                        )
                    )
                )
            )
            if explicit and looks_like_location:
                return CountryResolution(
                    country=explicit,
                    confidence="high",
                    evidence=(
                        "type=explicit_resume_header_country "
                        f"country={explicit.name}"
                    ),
                )
        return None

    # When city exists it is the primary correlation key. This prevents short
    # state codes such as ON/OR/ME from matching ordinary resume prose.
    if city_text:
        location_lines = [line for line in lines if _contains_token(line, city_text)]
    elif len(state_text) == 2:
        # A state code alone is not enough for a write unless the same line also
        # spells out the country (handled below).
        location_lines = [line for line in lines if _contains_token(line, state_text)]
    else:
        location_lines = [line for line in lines if _contains_token(line, state_text)]
    if not location_lines:
        return None

    # An explicit country on the corroborated location line is strongest and
    # safely resolves ambiguous region codes such as WA and IN.
    for line in location_lines:
        explicit = _explicit_country_on_line(line)
        if explicit:
            return CountryResolution(
                country=explicit,
                confidence="high",
                evidence=(
                    "type=explicit_country_on_correlated_location "
                    f"state={state_text!r} "
                    f"country={explicit.name}"
                ),
            )

    if (
        not state_upper
        or state_upper in AMBIGUOUS_REGION_CODES
        or (not city_text and len(state_text) == 2)
    ):
        return None

    matches = [
        country_name
        for regions, country_name in REGION_COUNTRIES
        if state_upper in regions
    ]
    if len(matches) != 1:
        return None

    definition = COUNTRIES[matches[0]]
    return CountryResolution(
        country=definition,
        confidence="high",
        evidence=(
            "type=resume_city_state_region_match "
            f"state={state_text!r} "
            f"country={definition.name}"
        ),
    )
