import re
import json
import logging
from typing import Dict, Optional, Tuple, Any

logger = logging.getLogger(__name__)


class AIMixin:

    def parse_resume_with_ai(self, resume_text: str) -> Dict[str, Any]:
        """
        Use AI to extract structured data from resume text

        Returns comprehensive candidate profile
        """
        if not self.openai_client:
            self.logger.warning("OpenAI not available, skipping AI resume parsing")
            return {}

        if not resume_text or len(resume_text.strip()) < 50:
            self.logger.warning("Resume text too short for AI parsing")
            return {}

        try:
            prompt = f"""Analyze this resume and extract structured candidate information.
Return a JSON object with the following fields (use null for missing data):

{{
    "first_name": "string",
    "last_name": "string",
    "email": "string",
    "phone": "string",
    "city": "string",
    "state": "string (2-letter code for US/Canada)",
    "country": "string",
    "current_title": "string (most recent job title)",
    "current_company": "string (most recent employer)",
    "years_experience": number (total years of professional experience),
    "skills": ["array", "of", "technical", "skills"],
    "education": [
        {{"degree": "string", "field": "string", "institution": "string", "year": number}}
    ],
    "certifications": ["array", "of", "certifications"],
    "work_history": [
        {{"title": "string", "company": "string", "start_year": number, "end_year": number, "current": boolean}}
    ],
    "summary": "2-3 sentence professional summary"
}}

CRITICAL — Name extraction rules (first_name / last_name):
- Return ONLY the candidate's actual personal name. Names typically appear on the first non-empty line and are often followed by post-nominal credentials such as "MBA", "PMP", "CTMP", "CFA", "CBAP", "PhD", "®", "™".
- If the name line includes credentials (example: "Akhil Reddy, CTMP, MBA, ODCP, CBAP®"), strip the credentials and return only the personal name parts: first_name="Akhil", last_name="Reddy".
- DO NOT use any of the following as the candidate's name:
    * Citizenship status: "Canadian Citizen", "US Citizen", "American Citizen", "British Citizen", "Indian Citizen", "Permanent Resident".
    * Work authorization: "Green Card", "H1B Visa", "Work Permit", "EAD", "OPT", "Authorized to Work".
    * Locations or addresses: "Toronto, ON", "New York, NY".
    * Job titles or company names.
    * Generic words: "Resume", "CV", "Curriculum Vitae", "Candidate", "Applicant".
- If the resume has no clear personal name, return null for both first_name and last_name. Returning null is preferred over guessing.

Resume text:
{resume_text[:20000]}
"""

            response = self.openai_client.chat.completions.create(
                model="gpt-5.4",
                messages=[
                    {"role": "system", "content": "You are an expert resume parser. Extract structured data accurately. Return only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                max_completion_tokens=4000
            )

            content = response.choices[0].message.content.strip()

            if content.startswith('```json'):
                content = content[7:]
            if content.startswith('```'):
                content = content[3:]
            if content.endswith('```'):
                content = content[:-3]

            parsed = json.loads(content)

            from utils.candidate_name_extraction import is_valid_name
            ai_first = parsed.get('first_name')
            ai_last = parsed.get('last_name')
            if (ai_first or ai_last) and not is_valid_name(ai_first, ai_last):
                self.logger.warning(
                    f"AI parser returned invalid candidate name "
                    f"'{ai_first} {ai_last}' (matches blocklist or "
                    f"failed validation). Nulling so deterministic "
                    f"fallback chain runs."
                )
                parsed['first_name'] = None
                parsed['last_name'] = None

            self.logger.info(f"AI parsed resume successfully: {parsed.get('first_name')} {parsed.get('last_name')}")
            return parsed

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse AI response as JSON: {e}")
            return {}
        except TimeoutError as e:
            self.logger.error(f"OpenAI API timeout (60s) during resume parsing: {e}")
            return {'_timeout_error': 'OpenAI API timeout - resume parsing took too long'}
        except Exception as e:
            error_type = type(e).__name__
            if 'timeout' in str(e).lower() or 'timed out' in str(e).lower():
                self.logger.error(f"OpenAI API timeout during resume parsing: {e}")
                return {'_timeout_error': 'OpenAI API timeout - resume parsing took too long'}
            self.logger.error(f"AI resume parsing error ({error_type}): {e}")
            return {}

    def _last_resort_ai_extraction(
        self,
        subject: str,
        sender: str,
        resume_filename: Optional[str],
        resume_text_preview: str,
        body_preview: str,
    ) -> Dict[str, Any]:
        """Layer 5: focused AI safety net for unusual emails.

        Fires only when subject regex, AI resume parser, filename parsing,
        and email-local-part parsing have all failed to recover a usable
        candidate name + contact pair.

        Uses gpt-4.1-mini with a tight JSON-only prompt and a strict
        15-second timeout. Returns ``{}`` on any failure — never raises.
        """
        if not self.openai_client:
            return {}
        if not (subject or resume_filename or resume_text_preview or body_preview):
            return {}

        prompt = (
            "Extract the candidate's name and contact info from the following "
            "job-board application signals. Return STRICT JSON with exactly "
            "these keys (use null when truly unknown):\n"
            '{"first_name": string|null, "last_name": string|null, '
            '"email": string|null, "phone": string|null}\n\n'
            "Rules:\n"
            "- first_name and last_name must be a real person\u2019s name parts. "
            "Do NOT return generic words like \u201cResume\u201d, \u201cCV\u201d, "
            "\u201cCandidate\u201d, \u201cApplicant\u201d, \u201cNone\u201d, or filler.\n"
            "- Preserve original casing and accents.\n"
            "- email must be a valid RFC-5322 address; phone must be 10+ digits.\n"
            "- Return null rather than guessing.\n\n"
            f"Email subject: {subject!r}\n"
            f"Email sender: {sender!r}\n"
            f"Resume filename: {resume_filename!r}\n"
            f"First chars of resume text:\n{resume_text_preview}\n\n"
            f"First chars of email body (HTML stripped):\n{body_preview}\n"
        )

        try:
            response = self.openai_client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You extract candidate identity from job board "
                            "applications. You return only valid JSON matching "
                            "the requested schema. You never invent data."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=300,
                timeout=15.0,
            )
            content = (response.choices[0].message.content or "").strip()
            if not content:
                return {}
            data = json.loads(content)
            for key in ("first_name", "last_name", "email", "phone"):
                if isinstance(data.get(key), str) and not data[key].strip():
                    data[key] = None
            self.logger.info(
                f"Last-resort AI extraction returned: "
                f"name={data.get('first_name')} {data.get('last_name')}, "
                f"email={data.get('email')}, phone={data.get('phone')}"
            )
            return data
        except json.JSONDecodeError as e:
            self.logger.warning(f"Last-resort AI returned non-JSON: {e}")
            return {}
        except Exception as e:
            self.logger.warning(f"Last-resort AI extraction failed ({type(e).__name__}): {e}")
            return {}

    def find_duplicate_candidate(self, email: str, phone: str, first_name: str, last_name: str,
                                  bullhorn_service) -> Tuple[Optional[int], float]:
        """
        Search Bullhorn for existing candidate using multi-layer matching.

        Returns:
            Tuple of (candidate_id, confidence_score)
            confidence_score: 1.0 for exact primary email match, 0.95 for email2/email3 match,
                            0.9 for phone match, 0.7+ for fuzzy name match
        """
        normalized_email = email.strip().lower() if email else ''
        normalized_phone = re.sub(r'\D', '', phone) if phone else ''

        self.logger.info(f"DUPLICATE CHECK: email='{normalized_email}', phone='{normalized_phone}', name='{first_name} {last_name}'")

        try:
            if normalized_email:
                results = bullhorn_service.search_candidates(email=normalized_email)
                if results and len(results) > 0:
                    match = results[0]
                    candidate_id = match.get('id')
                    matched_field = 'email'
                    existing_email = (match.get('email') or '').strip().lower()
                    existing_email2 = (match.get('email2') or '').strip().lower()
                    existing_email3 = (match.get('email3') or '').strip().lower()

                    if normalized_email == existing_email:
                        confidence = 1.0
                        matched_field = 'email (primary)'
                    elif normalized_email == existing_email2:
                        confidence = 0.95
                        matched_field = 'email2'
                    elif normalized_email == existing_email3:
                        confidence = 0.95
                        matched_field = 'email3'
                    else:
                        confidence = 0.95
                        matched_field = 'email (search match)'

                    self.logger.info(f"DUPLICATE FOUND by {matched_field}: candidate {candidate_id} "
                                   f"(existing: {match.get('firstName')} {match.get('lastName')}, "
                                   f"email={existing_email}), confidence={confidence}")
                    return candidate_id, confidence
                else:
                    self.logger.info(f"No email match found for '{normalized_email}' across email/email2/email3")

            if normalized_phone and len(normalized_phone) >= 10:
                results = bullhorn_service.search_candidates(phone=normalized_phone)
                if results and len(results) > 0:
                    candidate_id = results[0].get('id')
                    self.logger.info(f"DUPLICATE FOUND by phone: candidate {candidate_id} "
                                   f"(existing: {results[0].get('firstName')} {results[0].get('lastName')}, "
                                   f"phone={results[0].get('phone')}), confidence=0.9")
                    return candidate_id, 0.9
                else:
                    self.logger.info(f"No phone match found for '{normalized_phone}'")

            if first_name and last_name:
                results = bullhorn_service.search_candidates(
                    first_name=first_name,
                    last_name=last_name
                )
                if results and len(results) > 0:
                    confidence = self._ai_validate_duplicate(
                        first_name, last_name, email, phone, results[0]
                    )
                    if confidence >= 0.7:
                        candidate_id = results[0].get('id')
                        self.logger.info(f"DUPLICATE FOUND by name+AI: candidate {candidate_id} "
                                       f"(existing: {results[0].get('firstName')} {results[0].get('lastName')}), "
                                       f"confidence={confidence}")
                        return candidate_id, confidence
                    else:
                        self.logger.info(f"Name match found but AI confidence too low ({confidence}): "
                                       f"{results[0].get('firstName')} {results[0].get('lastName')} (ID: {results[0].get('id')})")

            self.logger.info(f"DUPLICATE CHECK COMPLETE: No duplicate found for {first_name} {last_name} ({normalized_email})")
            return None, 0.0

        except Exception as e:
            self.logger.error(f"DUPLICATE CHECK ERROR: {type(e).__name__}: {e} — falling through to new candidate creation")
            return None, 0.0

    def _ai_validate_duplicate(self, first_name: str, last_name: str, email: str,
                               phone: str, existing: Dict) -> float:
        """
        Use AI to validate if a name match is truly the same person

        Returns confidence score 0.0-1.0
        """
        if not self.openai_client:
            if (existing.get('firstName', '').lower() == first_name.lower() and
                existing.get('lastName', '').lower() == last_name.lower()):
                return 0.75
            return 0.5

        try:
            prompt = f"""Determine if these two candidate profiles are the same person.

New Candidate:
- Name: {first_name} {last_name}
- Email: {email or 'N/A'}
- Phone: {phone or 'N/A'}

Existing Candidate in Database:
- Name: {existing.get('firstName', '')} {existing.get('lastName', '')}
- Email: {existing.get('email', 'N/A')}
- Phone: {existing.get('phone', 'N/A')}
- Location: {existing.get('address', {}).get('city', 'N/A')}, {existing.get('address', {}).get('state', 'N/A')}

Return only a number between 0.0 and 1.0 representing the probability these are the same person.
Consider: name spelling variations, nicknames, contact info matches.
"""

            response = self.openai_client.chat.completions.create(
                model="gpt-5.4",
                messages=[
                    {"role": "system", "content": "You are a deduplication expert. Return only a decimal number."},
                    {"role": "user", "content": prompt}
                ],
                max_completion_tokens=10
            )

            confidence = float(response.choices[0].message.content.strip())
            return min(max(confidence, 0.0), 1.0)

        except Exception as e:
            self.logger.error(f"AI duplicate validation error: {e}")
            return 0.5

    def _build_enrichment_update(self, existing: Optional[Dict], new_data: Dict) -> Dict:
        """
        Build an enrichment-only update payload. Only includes fields that are
        blank/missing/empty on the existing record but populated in new_data.
        Never overwrites existing populated fields.

        Special handling:
        - 'description' (Resume pane): always update with latest resume HTML (approved overwrite)
        - 'address': merge sub-fields individually (city, state, etc.)
        - 'status': never change existing status

        If existing is None (API fetch failed), returns only the description update
        to avoid accidentally overwriting populated fields with a full payload.
        """
        if not existing:
            self.logger.warning("Could not fetch existing candidate — limiting update to description only to prevent overwrites")
            if new_data.get('description'):
                return {'description': new_data['description']}
            return {}

        enrichable_fields = [
            'phone', 'mobile', 'occupation', 'companyName', 'skillSet',
            'employmentPreference', 'email2', 'email3',
        ]

        enriched = {}

        for field in enrichable_fields:
            existing_val = existing.get(field)
            new_val = new_data.get(field)
            if new_val and not existing_val:
                enriched[field] = new_val
                self.logger.info(f"  Enriching blank field '{field}' with: {str(new_val)[:80]}")

        existing_addr = existing.get('address') or {}
        new_addr = new_data.get('address') or {}
        if new_addr and isinstance(new_addr, dict):
            addr_update = {}
            for addr_field in ['address1', 'address2', 'city', 'state', 'zip', 'countryID', 'countryName']:
                existing_addr_val = existing_addr.get(addr_field)
                new_addr_val = new_addr.get(addr_field)
                if new_addr_val and not existing_addr_val:
                    addr_update[addr_field] = new_addr_val
                    self.logger.info(f"  Enriching blank address.{addr_field} with: {new_addr_val}")
            if addr_update:
                enriched['address'] = addr_update

        if new_data.get('description'):
            enriched['description'] = new_data['description']
            self.logger.info(f"  Updating description (Resume pane) with latest resume ({len(new_data['description'])} chars)")

        return enriched
