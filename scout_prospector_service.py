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

    def execute(self, profile, criteria):
        import openai
        import os

        client = openai.OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

        system_prompt = """You are a B2B sales research analyst specializing in the staffing and recruitment industry. 
Your task is to identify companies that would be strong prospects for a staffing company based on the Ideal Client Profile (ICP) provided.

For each company you find, provide:
- Company name
- Industry
- Estimated employee count/size category
- Location (headquarters)
- Website URL if available
- Key decision-maker contacts (titles, not personal info)
- Current hiring activity (active job postings, growth signals)
- Why they're a good fit based on the ICP criteria
- A qualification score from 0-100

Return your findings as valid JSON with this structure:
{
  "companies": [
    {
      "name": "Company Name",
      "industry": "Industry",
      "size": "50-200 employees",
      "location": "City, State",
      "website": "https://example.com",
      "contacts": ["VP of HR", "Director of Talent Acquisition"],
      "hiring_activity": "Currently hiring for 15+ positions including software engineers and project managers",
      "fit_reason": "Strong match because...",
      "score": 85,
      "sources": ["https://source1.com"]
    }
  ],
  "summary": "Brief summary of findings and market observations"
}

Find 5-10 qualifying companies. Focus on quality over quantity. Only include companies with genuine hiring signals or staffing needs."""

        industries_str = ', '.join(criteria.get('industries', [])) or 'Any'
        sizes_str = ', '.join(criteria.get('company_sizes', [])) or 'Any'
        geos_str = ', '.join(criteria.get('geographies', [])) or 'Any'
        jobs_str = ', '.join(criteria.get('job_types', [])) or 'Any'
        signals_str = ', '.join(criteria.get('hiring_signals', [])) or 'Active job postings, growth indicators'
        additional = criteria.get('additional_criteria', '')

        user_prompt = f"""Research and identify companies matching this Ideal Client Profile:

**Profile Name:** {criteria.get('profile_name', 'Untitled')}
**Target Industries:** {industries_str}
**Company Sizes:** {sizes_str}
**Geographic Focus:** {geos_str}
**Job Types They Staff For:** {jobs_str}
**Hiring Signals to Look For:** {signals_str}
{f'**Additional Criteria:** {additional}' if additional else ''}

Search the web for companies that match these criteria. Look for active job postings, press releases about growth, and other hiring signals. Return your findings as JSON."""

        try:
            response = client.responses.create(
                model="gpt-4.1-mini",
                instructions=system_prompt,
                input=user_prompt,
                tools=[{"type": "web_search_preview"}],
                max_output_tokens=4096,
            )

            response_text = ''
            for item in response.output:
                if item.type == 'message':
                    for content_block in item.content:
                        if content_block.type == 'output_text':
                            response_text = content_block.text
                            break

            if not response_text:
                logger.warning("Empty response from AI research")
                return {'companies': [], 'summary': 'No results returned from AI research.'}

            clean_text = response_text.strip()
            if clean_text.startswith('```json'):
                clean_text = clean_text[7:]
            if clean_text.startswith('```'):
                clean_text = clean_text[3:]
            if clean_text.endswith('```'):
                clean_text = clean_text[:-3]
            clean_text = clean_text.strip()

            result = json.loads(clean_text)
            if 'companies' not in result:
                result = {'companies': [], 'summary': 'Response did not contain company data.'}

            for company in result.get('companies', []):
                score = company.get('score', 50)
                if not isinstance(score, (int, float)):
                    score = 50
                company['score'] = max(0, min(100, int(score)))

                company['website'] = sanitize_url(company.get('website'))
                company['sources'] = sanitize_url_list(company.get('sources', []))

            return result

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse AI research response: {e}")
            return {
                'companies': [],
                'summary': 'AI returned non-JSON response. Raw text available in run details.',
            }
        except Exception as e:
            logger.error(f"AI research API error: {e}")
            raise


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
        profile = ProspectorProfile.query.filter_by(id=profile_id, user_id=user.id).first()
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
        profile = ProspectorProfile.query.filter_by(id=profile_id, user_id=user.id).first()
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
        return ProspectorProfile.query.filter_by(id=profile_id, user_id=user.id).first()

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
            contacts_str = '; '.join(contacts) if contacts else ''
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
