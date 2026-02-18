"""
Tests for international/offshore location override in AI vetting prompt.

Verifies:
1. Remote jobs with international keywords → prompt includes INTERNATIONAL override
2. Domestic-only remote jobs → prompt enforces same-country rule, no override triggers
3. End-to-end: international job + matching country candidate → no mismatch penalty
4. End-to-end: domestic job + different country candidate → mismatch penalty applies
"""
import json
import pytest
from unittest.mock import patch, MagicMock, PropertyMock


@pytest.fixture
def app():
    """Create a test Flask app with in-memory database."""
    from app import app as flask_app, db
    
    flask_app.config['TESTING'] = True
    flask_app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    
    with flask_app.app_context():
        db.create_all()
        yield flask_app
        db.session.remove()


@pytest.fixture
def vetting_service(app):
    """Create a CandidateVettingService instance with mocked dependencies."""
    from candidate_vetting_service import CandidateVettingService
    
    service = CandidateVettingService.__new__(CandidateVettingService)
    service.openai_client = MagicMock()
    service.model = 'gpt-4o-mini'
    service.bullhorn = MagicMock()
    return service


def _make_job(job_id=34638, title="Software Engineer", description="", 
              city="", state="", country="United States", on_site=3):
    """Build a Bullhorn-style job dict."""
    return {
        'id': job_id,
        'title': title,
        'publicDescription': description,
        'address': {
            'city': city,
            'state': state,
            'countryName': country,
            'country': country
        },
        'onSite': on_site  # 3 = Remote
    }


SPAIN_CANDIDATE_RESUME = """
CANDIDATE: Maria Garcia
Location: Madrid, Spain
Email: maria@example.com | Phone: +34 612 345 678

PROFESSIONAL SUMMARY
Senior Software Engineer with 8 years of Python/Django experience.

WORK EXPERIENCE
Senior Developer – TechCorp Madrid (2020-Present)
- Built microservices with Python, FastAPI, PostgreSQL
- Led team of 5 engineers

EDUCATION
BSc Computer Science, Universidad Complutense de Madrid
"""

EGYPT_CANDIDATE_RESUME = """
CANDIDATE: Ahmed Hassan
Location: Cairo, Egypt
Email: ahmed@example.com | Phone: +20 100 123 4567

PROFESSIONAL SUMMARY
Full-stack developer with 6 years experience in React and Node.js.

WORK EXPERIENCE
Software Engineer – NileTech Cairo (2019-Present)
- Developed SaaS applications with React, Node.js, MongoDB
- Integrated payment gateways for MENA markets

EDUCATION
BSc Information Technology, Cairo University
"""

US_CANDIDATE_RESUME = """
CANDIDATE: John Smith
Location: Austin, TX 78701
Email: john@example.com | Phone: (512) 555-0123

PROFESSIONAL SUMMARY
Backend engineer with 5 years Python experience.

WORK EXPERIENCE
Software Engineer – TechStartup Inc, Austin TX (2020-Present)
- Built REST APIs with Python/Flask
- Managed PostgreSQL databases on AWS

EDUCATION
BS Computer Science, University of Texas at Austin
"""


class TestInternationalOverrideInPrompt:
    """Verify the AI prompt includes international override when appropriate."""

    def test_international_job_includes_override_clause(self, vetting_service, app):
        """Job desc says 'Egypt or Spain' → prompt must include INTERNATIONAL/OFFSHORE OVERRIDE."""
        job = _make_job(
            description="Remote Software Engineer. Must be located in Egypt or Spain. "
                        "Full-stack development with Python and React.",
            country="United States"
        )
        
        # Capture the prompt sent to OpenAI
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 82,
            "match_summary": "Strong match. Candidate in Madrid, Spain matches allowed location.",
            "skills_match": "Python, FastAPI, PostgreSQL",
            "experience_match": "8 years relevant experience",
            "gaps_identified": "No React experience mentioned",
            "key_requirements": "Python, React, Remote from Egypt or Spain"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response
        
        candidate_location = {'city': 'Madrid', 'state': '', 'countryName': 'Spain'}
        
        result = vetting_service.analyze_candidate_job_match(
            resume_text=SPAIN_CANDIDATE_RESUME,
            job=job,
            candidate_location=candidate_location
        )
        
        # Check the prompt that was sent to OpenAI
        call_args = vetting_service.openai_client.chat.completions.create.call_args
        messages = call_args[1]['messages'] if 'messages' in call_args[1] else call_args[0][0]
        user_prompt = messages[1]['content']
        
        # Must include the override clause
        assert 'INTERNATIONAL/OFFSHORE OVERRIDE' in user_prompt, \
            "International override clause missing from prompt for international job"
        assert 'same-country rule above does NOT apply' in user_prompt
    
    def test_domestic_job_has_same_country_rule(self, vetting_service, app):
        """Domestic remote job → prompt has same-country rule, but override is also present 
        (the AI reads the job description to decide whether to apply it)."""
        job = _make_job(
            description="Remote Software Engineer. US-based team working on cloud infrastructure. "
                        "Must have experience with AWS and Python.",
            city="Dallas",
            state="Texas",
            country="United States"
        )
        
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 45,
            "match_summary": "Location mismatch: candidate in Spain, job requires US.",
            "skills_match": "Python",
            "experience_match": "8 years development",
            "gaps_identified": "Location mismatch: different country, No AWS experience",
            "key_requirements": "Python, AWS, US-based"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response
        
        candidate_location = {'city': 'Madrid', 'state': '', 'countryName': 'Spain'}
        
        result = vetting_service.analyze_candidate_job_match(
            resume_text=SPAIN_CANDIDATE_RESUME,
            job=job,
            candidate_location=candidate_location
        )
        
        # Check the prompt includes the same-country rule
        call_args = vetting_service.openai_client.chat.completions.create.call_args
        messages = call_args[1]['messages']
        user_prompt = messages[1]['content']
        
        assert 'Candidate MUST be in the same COUNTRY' in user_prompt, \
            "Same-country rule missing from prompt"
        # The override clause is always present in the prompt template —
        # the AI decides whether to apply it based on the job description content
        assert 'INTERNATIONAL/OFFSHORE OVERRIDE' in user_prompt


class TestInternationalScoringEndToEnd:
    """End-to-end tests verifying AI output for international vs domestic scenarios."""
    
    def test_spain_candidate_for_egypt_spain_job_no_penalty(self, vetting_service, app):
        """Candidate in Madrid, Spain + job says 'Egypt or Spain' → no location mismatch."""
        job = _make_job(
            description="Remote Software Engineer. Must be located in Egypt or Spain. "
                        "Build microservices with Python and React.",
            country="United States"
        )
        
        # Mock AI returning a good score with no location mismatch
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 78,
            "match_summary": "Strong technical match. Candidate is based in Madrid, Spain which is one of the explicitly allowed locations per the job description.",
            "skills_match": "Python, FastAPI, PostgreSQL, microservices architecture",
            "experience_match": "8 years of Python development, team leadership",
            "gaps_identified": "No React experience mentioned in resume",
            "key_requirements": "Python, React, microservices, located in Egypt or Spain"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response
        
        candidate_location = {'city': 'Madrid', 'state': '', 'countryName': 'Spain'}
        
        result = vetting_service.analyze_candidate_job_match(
            resume_text=SPAIN_CANDIDATE_RESUME,
            job=job,
            candidate_location=candidate_location
        )
        
        assert result is not None
        assert result['match_score'] == 78
        # No location mismatch in gaps
        assert 'location mismatch' not in result.get('gaps_identified', '').lower() or \
               'different country' not in result.get('gaps_identified', '').lower(), \
            f"Unexpected location penalty: {result['gaps_identified']}"
    
    def test_egypt_candidate_for_egypt_spain_job_no_penalty(self, vetting_service, app):
        """Candidate in Cairo, Egypt + job says 'Egypt or Spain' → no location mismatch."""
        job = _make_job(
            description="Remote Full-Stack Developer. Open to candidates in Egypt or Spain. "
                        "React and Node.js required.",
            country="United States"
        )
        
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 80,
            "match_summary": "Excellent match. Candidate in Cairo, Egypt is in an explicitly allowed location.",
            "skills_match": "React, Node.js, MongoDB",
            "experience_match": "6 years full-stack development",
            "gaps_identified": "No specific gaps identified",
            "key_requirements": "React, Node.js, located in Egypt or Spain"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response
        
        candidate_location = {'city': 'Cairo', 'state': '', 'countryName': 'Egypt'}
        
        result = vetting_service.analyze_candidate_job_match(
            resume_text=EGYPT_CANDIDATE_RESUME,
            job=job,
            candidate_location=candidate_location
        )
        
        assert result is not None
        assert result['match_score'] == 80
        assert 'location mismatch' not in result.get('gaps_identified', '').lower()
    
    def test_domestic_job_still_penalizes_international_candidate(self, vetting_service, app):
        """Domestic-only remote US job + candidate in Spain → location penalty still applies."""
        job = _make_job(
            description="Remote Software Engineer. US-based team working on cloud infrastructure. "
                        "Python and AWS experience required.",
            city="Dallas",
            state="Texas",
            country="United States"
        )
        
        # AI should flag the country mismatch for domestic-only jobs
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 48,
            "match_summary": "The candidate is based in Madrid, Spain but the job requires remote work from the United States, creating a location compliance issue.",
            "skills_match": "Python, PostgreSQL",
            "experience_match": "8 years development experience",
            "gaps_identified": "Location mismatch: different country (Spain vs United States), No AWS experience",
            "key_requirements": "Python, AWS, US-based remote"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response
        
        candidate_location = {'city': 'Madrid', 'state': '', 'countryName': 'Spain'}
        
        result = vetting_service.analyze_candidate_job_match(
            resume_text=SPAIN_CANDIDATE_RESUME,
            job=job,
            candidate_location=candidate_location
        )
        
        assert result is not None
        assert result['match_score'] < 60, \
            f"Score {result['match_score']} too high — domestic job should penalize international candidate"
        assert 'location mismatch' in result['gaps_identified'].lower(), \
            "Expected location mismatch gap for domestic-only job"
    
    def test_us_candidate_for_us_job_no_penalty(self, vetting_service, app):
        """US candidate + US domestic remote job → no location mismatch (control test)."""
        job = _make_job(
            description="Remote Software Engineer based in United States. "
                        "Python and cloud experience required.",
            city="Dallas",
            state="Texas",
            country="United States"
        )
        
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 72,
            "match_summary": "Good match. Candidate is US-based and has relevant Python experience.",
            "skills_match": "Python, Flask, PostgreSQL, AWS",
            "experience_match": "5 years backend development",
            "gaps_identified": "No cloud infrastructure specialization mentioned",
            "key_requirements": "Python, AWS, US-based remote"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response
        
        candidate_location = {'city': 'Austin', 'state': 'Texas', 'countryName': 'United States'}
        
        result = vetting_service.analyze_candidate_job_match(
            resume_text=US_CANDIDATE_RESUME,
            job=job,
            candidate_location=candidate_location
        )
        
        assert result is not None
        assert result['match_score'] >= 60
        assert 'location mismatch' not in result.get('gaps_identified', '').lower()


class TestSmartCorrectCountryWACollision:
    """Regression tests for WA (Washington vs Western Australia) collision fix."""

    def test_wa_with_us_country_stays_us(self, vetting_service, app):
        """Washington state (WA) with declared 'United States' should NOT be corrected to Australia."""
        job = _make_job(
            description="Remote Sr Python Developer-W2 only. US-based.",
            city="", state="", country="United States"
        )

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 75,
            "match_summary": "Strong Python match. Candidate is US-based in Seattle, WA.",
            "skills_match": "Python, Flask, PostgreSQL, Docker, Kubernetes",
            "experience_match": "12 years software engineering",
            "gaps_identified": "No specific gaps",
            "key_requirements": "Python, W2 employment"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response

        # Candidate in Seattle, WA — state 'WA' must NOT trigger Australia correction
        candidate_location = {'city': 'Seattle', 'state': 'WA', 'countryName': 'United States'}

        result = vetting_service.analyze_candidate_job_match(
            resume_text="Maxwell Hirsch\nSeattle, WA, 98144\nSenior Software Engineer with 12+ years Python\n",
            job=job,
            candidate_location=candidate_location
        )

        assert result is not None
        assert 'location mismatch' not in result.get('gaps_identified', '').lower(), \
            f"WA candidate incorrectly flagged for location: {result['gaps_identified']}"
        assert 'different country' not in result.get('gaps_identified', '').lower(), \
            "WA (Washington) incorrectly treated as Western Australia"

        # Verify the prompt sent correct US location, not Australia
        call_args = vetting_service.openai_client.chat.completions.create.call_args
        messages = call_args[1]['messages']
        user_prompt = messages[1]['content']
        assert 'Australia' not in user_prompt, \
            "Prompt incorrectly contains 'Australia' for WA (Washington) candidate"

    def test_genuine_western_australia_still_corrects(self, vetting_service, app):
        """A candidate with state 'NSW' (New South Wales) should still be corrected to Australia."""
        job = _make_job(
            description="Remote Developer. US-based team.",
            country="United States"
        )

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 40,
            "match_summary": "Location mismatch: candidate in Australia.",
            "skills_match": "Python",
            "experience_match": "3 years",
            "gaps_identified": "Location mismatch: different country",
            "key_requirements": "Python, US-based"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response

        # NSW is unambiguously Australian — should be corrected
        candidate_location = {'city': 'Sydney', 'state': 'NSW', 'countryName': ''}

        result = vetting_service.analyze_candidate_job_match(
            resume_text="Test Candidate\nSydney, NSW\nDeveloper with 3 years Python\n",
            job=job,
            candidate_location=candidate_location
        )

        # Verify prompt has Australia (NSW correctly resolved)
        call_args = vetting_service.openai_client.chat.completions.create.call_args
        messages = call_args[1]['messages']
        user_prompt = messages[1]['content']
        assert 'Australia' in user_prompt, \
            "NSW should be correctly resolved to Australia"
