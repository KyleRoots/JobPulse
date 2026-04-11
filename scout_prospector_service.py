import csv
import io
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from urllib.parse import urlparse

from extensions import db
from models import ProspectorProfile, ProspectorRun, Prospect

logger = logging.getLogger(__name__)

ALLOWED_URL_SCHEMES = {'http', 'https', ''}


def sanitize_url(url):
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ALLOWED_URL_SCHEMES:
            return None
        if not parsed.netloc and not parsed.path:
            return None
        if not parsed.scheme:
            url = 'https://' + url
        return url
    except Exception:
        return None


def sanitize_url_list(urls):
    if not urls or not isinstance(urls, list):
        return []
    return [u for u in (sanitize_url(u) for u in urls) if u]


class ResearchProvider(ABC):
    @abstractmethod
    def execute(self, profile, criteria):
        pass


class WebSearchProvider(ResearchProvider):

    SYSTEM_PROMPT = """You are a B2B sales research analyst specializing in the staffing and recruitment industry. 
Your task is to identify companies that would be strong prospects for a staffing company based on the Ideal Client Profile (ICP) provided.

For each company you find, provide:
- Company name
- Industry
- Estimated employee count/size category
- Location (headquarters)
- Website URL if available
- Key decision-maker contacts with as much detail as you can find publicly:
  - Full name (if publicly available on the company website, LinkedIn, or press releases)
  - Job title / role
  - LinkedIn profile URL — ONLY include a LinkedIn URL if you actually found and verified it in your web search results. NEVER guess or fabricate LinkedIn URLs based on name patterns (e.g., do not construct "/in/firstname-lastname" or "/in/firstnamelastname"). If you cannot find a verified LinkedIn profile, return an empty string.
  - Email address (only if publicly listed on company website, press releases, or directories)
  - Phone number (only if publicly listed on company website, contact pages, or directories — never guess)
- Current hiring activity (active job postings, growth signals)
- Why they're a good fit based on the ICP criteria
- A qualification score from 0-100
- Source URLs where you found the information

Return your findings as valid JSON with this structure:
{
  "companies": [
    {
      "name": "Company Name",
      "industry": "Industry",
      "size": "50-200 employees",
      "location": "City, State",
      "website": "https://example.com",
      "contacts": [
        {
          "name": "Jane Smith",
          "title": "VP of Human Resources",
          "linkedin": "https://www.linkedin.com/in/verified-profile-url",
          "email": "jane.smith@example.com",
          "phone": "+1-555-123-4567"
        },
        {
          "name": "",
          "title": "Director of Talent Acquisition",
          "linkedin": "",
          "email": "",
          "phone": ""
        }
      ],
      "hiring_activity": "Currently hiring for 15+ positions including software engineers and project managers",
      "fit_reason": "Strong match because...",
      "score": 85,
      "sources": ["https://source1.com"]
    }
  ],
  "summary": "Brief summary of findings and market observations"
}

IMPORTANT: For contacts, always return objects with name/title/linkedin/email/phone fields. Use empty strings for any fields you cannot find — never omit the fields. 
CRITICAL LINKEDIN RULE: Only include LinkedIn URLs that you actually found and clicked through in your web search. NEVER construct LinkedIn URLs by guessing the slug from a person's name. If you searched for someone's LinkedIn and couldn't find a verified profile URL, return an empty string for the linkedin field.
Only include email addresses and phone numbers that are publicly available — do not guess or fabricate them.
Only include companies with genuine hiring signals or staffing needs."""

    SEARCH_ANGLES = [
        {
            'label': 'Job Boards & Active Postings',
            'instruction': (
                "Focus on searching job boards (Indeed, LinkedIn Jobs, Glassdoor, ZipRecruiter, company career pages) "
                "for companies actively posting positions matching the ICP criteria. "
                "Search for recent job listings in the target industries and geographies. "
                "Look for companies with multiple open roles — this signals active staffing needs."
            ),
        },
        {
            'label': 'Company Growth & Expansion News',
            'instruction': (
                "Focus on business news, press releases, SEC filings, and industry publications. "
                "Search for companies announcing expansions, new office openings, funding rounds, "
                "major contract wins, mergers/acquisitions, or rapid revenue growth in the target industries and geographies. "
                "These growth signals indicate upcoming or current hiring needs even if jobs aren't posted yet."
            ),
        },
        {
            'label': 'Industry Directories & Associations',
            'instruction': (
                "Focus on industry directories, trade association member lists, business registries, "
                "government contract databases, and professional networks. "
                "Search for companies in the target industries and geographies using directories like "
                "local chambers of commerce, industry-specific associations, government vendor lists, "
                "and business databases. Cross-reference with any hiring or growth signals you can find."
            ),
        },
    ]

    MAX_COMPANIES_TOTAL = 50
    COMPANIES_PER_PASS = 20

    def execute(self, profile, criteria):
        import openai
        import os

        client = openai.OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

        industries_str = ', '.join(criteria.get('industries', [])) or 'Any'
        sizes_str = ', '.join(criteria.get('company_sizes', [])) or 'Any'
        geos_str = ', '.join(criteria.get('geographies', [])) or 'Any'
        jobs_str = ', '.join(criteria.get('job_types', [])) or 'Any'
        signals_str = ', '.join(criteria.get('hiring_signals', [])) or 'Active job postings, growth indicators'
        additional = criteria.get('additional_criteria', '')

        all_companies = []
        seen_names = set()
        summaries = []

        for pass_num, angle in enumerate(self.SEARCH_ANGLES, 1):
            remaining = self.MAX_COMPANIES_TOTAL - len(all_companies)
            if remaining <= 0:
                break

            target = min(self.COMPANIES_PER_PASS, remaining)

            exclude_instruction = ''
            if seen_names:
                exclude_list = ', '.join(sorted(seen_names))
                exclude_instruction = (
                    f"\n\nIMPORTANT: Do NOT include any of these companies (already found in previous searches): "
                    f"{exclude_list}. Find DIFFERENT companies not in this list."
                )

            pass_system = (
                self.SYSTEM_PROMPT
                + f"\n\nFind up to {target} qualifying companies. Be thorough and cast a wide net."
            )

            pass_prompt = f"""Research and identify companies matching this Ideal Client Profile:

**Profile Name:** {criteria.get('profile_name', 'Untitled')}
**Target Industries:** {industries_str}
**Company Sizes:** {sizes_str}
**Geographic Focus:** {geos_str}
**Job Types They Staff For:** {jobs_str}
**Hiring Signals to Look For:** {signals_str}
{f'**Additional Criteria:** {additional}' if additional else ''}

**SEARCH FOCUS (Pass {pass_num} of {len(self.SEARCH_ANGLES)} — {angle['label']}):**
{angle['instruction']}{exclude_instruction}

Search the web thoroughly for companies that match these criteria. Return your findings as JSON."""

            logger.info(f"Research pass {pass_num}/{len(self.SEARCH_ANGLES)}: {angle['label']} (target: {target} companies)")

            try:
                pass_result = self._execute_single_pass(client, pass_system, pass_prompt)
                pass_companies = pass_result.get('companies', [])
                pass_summary = pass_result.get('summary', '')

                new_count = 0
                for company in pass_companies:
                    name_key = (company.get('name') or '').strip().lower()
                    if name_key and name_key not in seen_names:
                        seen_names.add(name_key)
                        all_companies.append(company)
                        new_count += 1
                    elif name_key in seen_names:
                        logger.debug(f"Dedup: skipping '{company.get('name')}' (already found)")

                if pass_summary:
                    summaries.append(f"**{angle['label']}:** {pass_summary}")

                logger.info(f"Pass {pass_num} complete: {len(pass_companies)} returned, {new_count} new (total: {len(all_companies)})")

            except Exception as e:
                logger.error(f"Research pass {pass_num} ({angle['label']}) failed: {e}")
                summaries.append(f"**{angle['label']}:** Search pass failed — {str(e)[:100]}")
                continue

        all_companies.sort(key=lambda c: c.get('score', 0), reverse=True)

        combined_summary = ' | '.join(summaries) if summaries else 'Research complete.'
        combined_summary += f" | Total unique prospects found: {len(all_companies)} across {len(self.SEARCH_ANGLES)} search passes."

        return {
            'companies': all_companies[:self.MAX_COMPANIES_TOTAL],
            'summary': combined_summary,
        }

    def _execute_single_pass(self, client, system_prompt, user_prompt):
        response = client.responses.create(
            model="gpt-5.4",
            instructions=system_prompt,
            input=user_prompt,
            tools=[{"type": "web_search_preview"}],
            max_output_tokens=16384,
        )

        response_text = ''
        for item in response.output:
            if item.type == 'message':
                for content_block in item.content:
                    if content_block.type == 'output_text':
                        response_text = content_block.text
                        break

        if not response_text:
            logger.warning("Empty response from AI research pass")
            return {'companies': [], 'summary': 'No results returned.'}

        clean_text = response_text.strip()
        if clean_text.startswith('```json'):
            clean_text = clean_text[7:]
        if clean_text.startswith('```'):
            clean_text = clean_text[3:]
        if clean_text.endswith('```'):
            clean_text = clean_text[:-3]
        clean_text = clean_text.strip()

        try:
            result = json.loads(clean_text)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI research pass response: {e}")
            return {'companies': [], 'summary': 'AI returned non-JSON response in this pass.'}

        if 'companies' not in result:
            return {'companies': [], 'summary': result.get('summary', 'No company data in response.')}

        for company in result.get('companies', []):
            score = company.get('score', 50)
            if not isinstance(score, (int, float)):
                score = 50
            company['score'] = max(0, min(100, int(score)))

            company['website'] = sanitize_url(company.get('website'))
            company['sources'] = sanitize_url_list(company.get('sources', []))

            raw_contacts = company.get('contacts', [])
            normalized = []
            for c in raw_contacts:
                if isinstance(c, dict):
                    normalized.append({
                        'name': (c.get('name') or '').strip(),
                        'title': (c.get('title') or '').strip(),
                        'linkedin': sanitize_url(c.get('linkedin')) or '',
                        'email': (c.get('email') or '').strip(),
                        'phone': (c.get('phone') or '').strip(),
                    })
                elif isinstance(c, str):
                    normalized.append({
                        'name': '',
                        'title': c.strip(),
                        'linkedin': '',
                        'email': '',
                        'phone': '',
                    })
            company['contacts'] = normalized

        return result


class ProspectorService:

    def __init__(self, provider=None):
        self._provider = provider or WebSearchProvider()

    def create_profile(self, user, name, description=None, industries=None,
                       company_sizes=None, geographies=None, job_types=None,
                       hiring_signals=None, additional_criteria=None):
        profile = ProspectorProfile(
            user_id=user.id,
            company=user.company or 'Unknown',
            name=name,
            description=description,
            additional_criteria=additional_criteria,
        )
        profile.set_industries(industries or [])
        profile.set_company_sizes(company_sizes or [])
        profile.set_geographies(geographies or [])
        profile.set_job_types(job_types or [])
        profile.set_hiring_signals(hiring_signals or [])
        db.session.add(profile)
        db.session.commit()
        logger.info(f"Created ProspectorProfile {profile.id} '{name}' for user {user.id}")
        return profile

    def update_profile(self, profile_id, user, **kwargs):
        profile = ProspectorProfile.query.filter_by(
            id=profile_id, company=user.company or 'Unknown'
        ).first()
        if not profile:
            return None
        for field in ['name', 'description', 'additional_criteria']:
            if field in kwargs:
                setattr(profile, field, kwargs[field])
        for list_field in ['industries', 'company_sizes', 'geographies', 'job_types', 'hiring_signals']:
            if list_field in kwargs:
                setter = getattr(profile, f'set_{list_field}')
                setter(kwargs[list_field])
        db.session.commit()
        return profile

    def delete_profile(self, profile_id, user):
        profile = ProspectorProfile.query.filter_by(
            id=profile_id, company=user.company or 'Unknown'
        ).first()
        if not profile:
            return False
        db.session.delete(profile)
        db.session.commit()
        return True

    def get_user_profiles(self, user):
        return ProspectorProfile.query.filter_by(
            company=user.company or 'Unknown', is_active=True
        ).order_by(ProspectorProfile.updated_at.desc()).all()

    def get_profile(self, profile_id, user):
        return ProspectorProfile.query.filter_by(
            id=profile_id, company=user.company or 'Unknown'
        ).first()

    def get_user_prospects(self, user, status=None, profile_id=None, search=None,
                           sort_by='qualification_score', sort_dir='desc'):
        query = Prospect.query.filter_by(company=user.company or 'Unknown')
        if status and status != 'all':
            query = query.filter_by(status=status)
        if profile_id:
            query = query.filter_by(profile_id=profile_id)
        if search:
            search_term = f'%{search}%'
            query = query.filter(
                db.or_(
                    Prospect.company_name.ilike(search_term),
                    Prospect.industry.ilike(search_term),
                    Prospect.location.ilike(search_term),
                )
            )
        if sort_by == 'qualification_score':
            order = Prospect.qualification_score.desc() if sort_dir == 'desc' else Prospect.qualification_score.asc()
        elif sort_by == 'company_name':
            order = Prospect.company_name.asc() if sort_dir == 'asc' else Prospect.company_name.desc()
        elif sort_by == 'created_at':
            order = Prospect.created_at.desc() if sort_dir == 'desc' else Prospect.created_at.asc()
        else:
            order = Prospect.qualification_score.desc()
        return query.order_by(order).all()

    def get_prospect(self, prospect_id, user):
        return Prospect.query.filter_by(id=prospect_id, company=user.company or 'Unknown').first()

    def update_prospect(self, prospect_id, user, status=None, notes=None):
        prospect = Prospect.query.filter_by(id=prospect_id, company=user.company or 'Unknown').first()
        if not prospect:
            return None
        if status and status in Prospect.VALID_STATUSES:
            prospect.status = status
        if notes is not None:
            prospect.notes = notes
        db.session.commit()
        return prospect

    def delete_prospect(self, prospect_id, user):
        prospect = Prospect.query.filter_by(id=prospect_id, company=user.company or 'Unknown').first()
        if not prospect:
            return False
        db.session.delete(prospect)
        db.session.commit()
        return True

    def get_prospect_stats(self, user):
        base = Prospect.query.filter_by(company=user.company or 'Unknown')
        total = base.count()
        hot = base.filter_by(status='hot').count()
        warm = base.filter_by(status='warm').count()
        contacted = base.filter_by(status='contacted').count()
        return {
            'total': total,
            'hot': hot,
            'warm': warm,
            'contacted': contacted,
        }

    def run_research(self, profile, user):
        run = ProspectorRun(
            profile_id=profile.id,
            user_id=user.id,
            company=user.company or 'Unknown',
            status='running',
            started_at=datetime.utcnow(),
        )
        db.session.add(run)
        db.session.commit()

        try:
            search_criteria = self._build_search_criteria(profile)
            run.search_query_used = json.dumps(search_criteria, indent=2)
            db.session.commit()

            results = self._provider.execute(profile, search_criteria)

            prospects_created = 0
            for company_data in results.get('companies', []):
                prospect = Prospect(
                    run_id=run.id,
                    profile_id=profile.id,
                    user_id=user.id,
                    company=user.company or 'Unknown',
                    company_name=company_data.get('name', 'Unknown Company'),
                    industry=company_data.get('industry'),
                    estimated_size=company_data.get('size'),
                    location=company_data.get('location'),
                    website=company_data.get('website'),
                    key_contacts=json.dumps(company_data.get('contacts', [])),
                    hiring_activity=company_data.get('hiring_activity'),
                    fit_reason=company_data.get('fit_reason'),
                    qualification_score=company_data.get('score', 50),
                    source_urls=json.dumps(company_data.get('sources', [])),
                    status='new',
                )
                db.session.add(prospect)
                prospects_created += 1

            run.status = 'completed'
            run.prospects_found = prospects_created
            run.ai_summary = results.get('summary', '')
            run.completed_at = datetime.utcnow()
            db.session.commit()
            logger.info(f"Research run {run.id} completed: {prospects_created} prospects found")
            return run

        except Exception as e:
            logger.error(f"Research run {run.id} failed: {e}")
            run.status = 'failed'
            run.error_message = str(e)
            run.completed_at = datetime.utcnow()
            db.session.commit()
            return run

    def _build_search_criteria(self, profile):
        return {
            'profile_name': profile.name,
            'industries': profile.get_industries(),
            'company_sizes': profile.get_company_sizes(),
            'geographies': profile.get_geographies(),
            'job_types': profile.get_job_types(),
            'hiring_signals': profile.get_hiring_signals(),
            'additional_criteria': profile.additional_criteria or '',
        }

    def refine_criteria(self, description):
        import openai
        import os

        if not description or not description.strip():
            return None

        client = openai.OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

        prompt = f"""A staffing company recruiter described their ideal client as:

"{description}"

Based on this description, suggest concrete search criteria for finding prospect companies. Return valid JSON with:
{{
  "suggested_industries": ["Industry1", "Industry2"],
  "suggested_sizes": ["51-200 employees", "201-500 employees"],
  "suggested_geographies": ["City, State"],
  "suggested_job_types": ["Role1", "Role2"],
  "suggested_signals": ["Active job postings", "Recent funding"],
  "refinement_notes": "Brief explanation of how you interpreted their description"
}}

Only include fields you can confidently infer. Use standard industry names and realistic values."""

        try:
            response = client.responses.create(
                model="gpt-4.1-mini",
                input=prompt,
                max_output_tokens=1024,
            )

            response_text = ''
            for item in response.output:
                if item.type == 'message':
                    for content_block in item.content:
                        if content_block.type == 'output_text':
                            response_text = content_block.text
                            break

            if not response_text:
                return None

            clean_text = response_text.strip()
            if clean_text.startswith('```json'):
                clean_text = clean_text[7:]
            if clean_text.startswith('```'):
                clean_text = clean_text[3:]
            if clean_text.endswith('```'):
                clean_text = clean_text[:-3]

            return json.loads(clean_text.strip())

        except Exception as e:
            logger.error(f"ICP refinement failed: {e}")
            return None

    def export_prospects_csv(self, user, profile_id=None, status=None):
        prospects = self.get_user_prospects(user, status=status, profile_id=profile_id)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'Company Name', 'Industry', 'Size', 'Location', 'Website',
            'Qualification Score', 'Status', 'Hiring Activity',
            'Fit Reason', 'Notes', 'Key Contacts', 'Found Date'
        ])
        for p in prospects:
            contacts = p.get_key_contacts()
            contact_parts = []
            for c in contacts:
                parts = []
                if c.get('name'):
                    parts.append(c['name'])
                if c.get('title'):
                    parts.append(c['title'])
                if c.get('email'):
                    parts.append(c['email'])
                if c.get('phone'):
                    parts.append(c['phone'])
                if c.get('linkedin'):
                    parts.append(c['linkedin'])
                contact_parts.append(' | '.join(parts) if parts else '')
            contacts_str = '; '.join(contact_parts)
            writer.writerow([
                p.company_name,
                p.industry or '',
                p.estimated_size or '',
                p.location or '',
                p.website or '',
                p.qualification_score,
                p.status,
                p.hiring_activity or '',
                p.fit_reason or '',
                p.notes or '',
                contacts_str,
                p.created_at.strftime('%Y-%m-%d') if p.created_at else '',
            ])
        return output.getvalue()

    def get_run_history(self, user, profile_id=None):
        query = ProspectorRun.query.filter_by(company=user.company or 'Unknown')
        if profile_id:
            query = query.filter_by(profile_id=profile_id)
        return query.order_by(ProspectorRun.created_at.desc()).limit(20).all()
