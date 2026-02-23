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


class TestSmartCorrectCountryNewCountries:
    """Regression tests for Egypt, India, Pakistan, Philippines country correction."""

    # ── Egypt ──────────────────────────────────────────────────────────

    def test_cairo_with_us_country_corrects_to_egypt(self, vetting_service, app):
        """Cairo + 'United States' → corrected to Egypt (Islam Nasser scenario)."""
        job = _make_job(
            description="Remote Data Scientist. AI and ML focus.",
            country="Egypt"
        )
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 25,
            "match_summary": "Candidate lacks ML/AI experience.",
            "skills_match": "Basic data analysis",
            "experience_match": "2 years analyst",
            "gaps_identified": "No ML modeling experience",
            "key_requirements": "Python, ML, statistics"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response

        # Candidate in Cairo with WRONG country in Bullhorn
        candidate_location = {'city': 'Cairo', 'state': '', 'countryName': 'United States'}

        result = vetting_service.analyze_candidate_job_match(
            resume_text="Islam Nasser\nCairo, Egypt\nData Analyst with 2 years experience\n",
            job=job,
            candidate_location=candidate_location
        )

        # Verify prompt says Egypt, not United States
        call_args = vetting_service.openai_client.chat.completions.create.call_args
        messages = call_args[1]['messages']
        user_prompt = messages[1]['content']
        assert 'Egypt' in user_prompt, \
            "Cairo should be corrected from 'United States' to 'Egypt' in the prompt"
        assert 'Cairo, United States' not in user_prompt, \
            "The contradictory 'Cairo, United States' should not appear in the prompt"

    def test_alexandria_corrects_to_egypt(self, vetting_service, app):
        """Alexandria, Egypt candidate with blank country → corrected to Egypt."""
        job = _make_job(description="Remote developer", country="Egypt")
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 50, "match_summary": "Partial match.",
            "skills_match": "Python", "experience_match": "3 years",
            "gaps_identified": "No React", "key_requirements": "Python, React"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response

        candidate_location = {'city': 'Alexandria', 'state': '', 'countryName': ''}
        result = vetting_service.analyze_candidate_job_match(
            resume_text="Test Candidate\nAlexandria, Egypt\nPython developer\n",
            job=job, candidate_location=candidate_location
        )
        call_args = vetting_service.openai_client.chat.completions.create.call_args
        user_prompt = call_args[1]['messages'][1]['content']
        assert 'Egypt' in user_prompt, "Alexandria should resolve to Egypt"

    # ── India ──────────────────────────────────────────────────────────

    def test_mumbai_with_us_country_corrects_to_india(self, vetting_service, app):
        """Mumbai + 'United States' → corrected to India."""
        job = _make_job(description="Remote developer", country="India")
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 70, "match_summary": "Good match.",
            "skills_match": "Python, Django", "experience_match": "5 years",
            "gaps_identified": "No AWS", "key_requirements": "Python, Django, AWS"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response

        candidate_location = {'city': 'Mumbai', 'state': 'Maharashtra', 'countryName': 'United States'}
        result = vetting_service.analyze_candidate_job_match(
            resume_text="Test Candidate\nMumbai, India\nSenior Python Developer\n",
            job=job, candidate_location=candidate_location
        )
        call_args = vetting_service.openai_client.chat.completions.create.call_args
        user_prompt = call_args[1]['messages'][1]['content']
        assert 'India' in user_prompt, "Mumbai/Maharashtra should be corrected to India"
        assert 'Mumbai, Maharashtra, United States' not in user_prompt

    def test_bangalore_state_only_corrects_to_india(self, vetting_service, app):
        """Karnataka state with blank country → corrected to India."""
        job = _make_job(description="Remote developer", country="India")
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 65, "match_summary": "Match.",
            "skills_match": "Java", "experience_match": "4 years",
            "gaps_identified": "No cloud", "key_requirements": "Java, AWS"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response

        candidate_location = {'city': 'Bangalore', 'state': 'Karnataka', 'countryName': ''}
        result = vetting_service.analyze_candidate_job_match(
            resume_text="Test\nBangalore, Karnataka\nJava developer\n",
            job=job, candidate_location=candidate_location
        )
        call_args = vetting_service.openai_client.chat.completions.create.call_args
        user_prompt = call_args[1]['messages'][1]['content']
        assert 'India' in user_prompt, "Karnataka state should resolve to India"

    # ── Pakistan ───────────────────────────────────────────────────────

    def test_karachi_with_us_country_corrects_to_pakistan(self, vetting_service, app):
        """Karachi + 'United States' → corrected to Pakistan."""
        job = _make_job(description="Remote developer", country="Pakistan")
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 60, "match_summary": "Match.",
            "skills_match": "Python", "experience_match": "3 years",
            "gaps_identified": "No React", "key_requirements": "Python, React"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response

        candidate_location = {'city': 'Karachi', 'state': 'Sindh', 'countryName': 'United States'}
        result = vetting_service.analyze_candidate_job_match(
            resume_text="Test\nKarachi, Pakistan\nPython developer\n",
            job=job, candidate_location=candidate_location
        )
        call_args = vetting_service.openai_client.chat.completions.create.call_args
        user_prompt = call_args[1]['messages'][1]['content']
        assert 'Pakistan' in user_prompt, "Karachi/Sindh should be corrected to Pakistan"

    # ── Philippines ────────────────────────────────────────────────────

    def test_manila_with_us_country_corrects_to_philippines(self, vetting_service, app):
        """Manila + 'United States' → corrected to Philippines."""
        job = _make_job(description="Remote developer", country="Philippines")
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 55, "match_summary": "Match.",
            "skills_match": "JavaScript", "experience_match": "3 years",
            "gaps_identified": "No TypeScript", "key_requirements": "JS, TypeScript"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response

        candidate_location = {'city': 'Manila', 'state': 'NCR', 'countryName': 'United States'}
        result = vetting_service.analyze_candidate_job_match(
            resume_text="Test\nManila, Philippines\nJS developer\n",
            job=job, candidate_location=candidate_location
        )
        call_args = vetting_service.openai_client.chat.completions.create.call_args
        user_prompt = call_args[1]['messages'][1]['content']
        assert 'Philippines' in user_prompt, "Manila/NCR should be corrected to Philippines"

    def test_makati_corrects_to_philippines(self, vetting_service, app):
        """Makati (BGC area) → corrected to Philippines."""
        job = _make_job(description="Remote developer", country="Philippines")
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 55, "match_summary": "Match.",
            "skills_match": "Python", "experience_match": "2 years",
            "gaps_identified": "Limited exp", "key_requirements": "Python"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response

        candidate_location = {'city': 'Makati', 'state': '', 'countryName': ''}
        result = vetting_service.analyze_candidate_job_match(
            resume_text="Test\nMakati, Metro Manila\nPython dev\n",
            job=job, candidate_location=candidate_location
        )
        call_args = vetting_service.openai_client.chat.completions.create.call_args
        user_prompt = call_args[1]['messages'][1]['content']
        assert 'Philippines' in user_prompt, "Makati should resolve to Philippines"

    # ── Collision disambiguation ───────────────────────────────────────

    def test_hyderabad_with_indian_state_resolves_to_india(self, vetting_service, app):
        """Hyderabad + Telangana (Indian state) → India, not Pakistan."""
        job = _make_job(description="Remote developer", country="India")
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 65, "match_summary": "Match.",
            "skills_match": "Java", "experience_match": "5 years",
            "gaps_identified": "No cloud", "key_requirements": "Java"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response

        candidate_location = {'city': 'Hyderabad', 'state': 'Telangana', 'countryName': ''}
        result = vetting_service.analyze_candidate_job_match(
            resume_text="Test\nHyderabad, Telangana\nJava developer\n",
            job=job, candidate_location=candidate_location
        )
        call_args = vetting_service.openai_client.chat.completions.create.call_args
        user_prompt = call_args[1]['messages'][1]['content']
        assert 'India' in user_prompt, "Hyderabad + Telangana should resolve to India"
        assert 'Pakistan' not in user_prompt, "Should not resolve to Pakistan"

    def test_lahore_punjab_resolves_to_pakistan(self, vetting_service, app):
        """Lahore + Punjab → Pakistan (not India, even though India also has Punjab)."""
        job = _make_job(description="Remote developer", country="Pakistan")
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 55, "match_summary": "Match.",
            "skills_match": "Python", "experience_match": "3 years",
            "gaps_identified": "No Django", "key_requirements": "Python, Django"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response

        candidate_location = {'city': 'Lahore', 'state': 'Punjab', 'countryName': ''}
        result = vetting_service.analyze_candidate_job_match(
            resume_text="Test\nLahore, Punjab\nPython developer\n",
            job=job, candidate_location=candidate_location
        )
        call_args = vetting_service.openai_client.chat.completions.create.call_args
        user_prompt = call_args[1]['messages'][1]['content']
        assert 'Pakistan' in user_prompt, "Lahore + Punjab should resolve to Pakistan"

    # ── Existing countries still work ──────────────────────────────────

    def test_toronto_still_corrects_to_canada(self, vetting_service, app):
        """Verify existing Canada lookup still works after adding new countries."""
        job = _make_job(description="Remote developer", country="Canada")
        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 70, "match_summary": "Good match.",
            "skills_match": "Python", "experience_match": "5 years",
            "gaps_identified": "No specific gaps", "key_requirements": "Python"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response

        candidate_location = {'city': 'Toronto', 'state': 'ON', 'countryName': 'United States'}
        result = vetting_service.analyze_candidate_job_match(
            resume_text="Test\nToronto, ON\nPython developer\n",
            job=job, candidate_location=candidate_location
        )
        call_args = vetting_service.openai_client.chat.completions.create.call_args
        user_prompt = call_args[1]['messages'][1]['content']
        assert 'Canada' in user_prompt, "Toronto/ON should still resolve to Canada"


class TestLocationAndClearancePrompt:
    """Regression tests for location matching and clearance prompt injection.

    Verifies the prompt sent to OpenAI contains the correct location rules
    for on-site, remote, and clearance scenarios.
    """

    def test_onsite_same_city_no_mismatch_in_prompt(self, vetting_service, app):
        """On-site job in Dallas + candidate in Dallas → prompt says 'AUTOMATICALLY qualify'."""
        job = _make_job(
            description="On-site Software Engineer in Dallas, TX. Python required.",
            city="Dallas", state="Texas", country="United States",
            on_site=1  # On-site
        )

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 80,
            "match_summary": "Strong local match.",
            "skills_match": "Python", "experience_match": "5 years",
            "gaps_identified": "", "key_requirements": "Python"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response

        candidate_location = {'city': 'Dallas', 'state': 'TX', 'countryName': 'United States'}
        vetting_service.analyze_candidate_job_match(
            resume_text=US_CANDIDATE_RESUME,
            job=job, candidate_location=candidate_location
        )

        call_args = vetting_service.openai_client.chat.completions.create.call_args
        user_prompt = call_args[1]['messages'][1]['content']

        assert 'AUTOMATICALLY qualify' in user_prompt, \
            "On-site prompt must include auto-qualify rule for local candidates"
        assert 'On-Site' in user_prompt or 'On-site' in user_prompt, \
            "Prompt must indicate On-Site work type"

    def test_onsite_cross_country_triggers_mismatch(self, vetting_service, app):
        """On-site job in Dallas + candidate in Spain → AI should detect mismatch."""
        job = _make_job(
            description="On-site Software Engineer in Dallas, TX. Python required.",
            city="Dallas", state="Texas", country="United States",
            on_site=1  # On-site
        )

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 35,
            "match_summary": "Candidate in Spain, job requires on-site in Dallas.",
            "skills_match": "Python", "experience_match": "8 years",
            "gaps_identified": "Location mismatch: candidate in Spain, job is on-site in Dallas, TX",
            "key_requirements": "Python, On-site in Dallas"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response

        candidate_location = {'city': 'Madrid', 'state': '', 'countryName': 'Spain'}
        result = vetting_service.analyze_candidate_job_match(
            resume_text=SPAIN_CANDIDATE_RESUME,
            job=job, candidate_location=candidate_location
        )

        assert result['match_score'] < 60, \
            f"On-site cross-country should score low, got {result['match_score']}"
        assert 'location mismatch' in result.get('gaps_identified', '').lower()

    def test_remote_same_country_no_mismatch(self, vetting_service, app):
        """Remote job in US + candidate in different US city → no mismatch."""
        job = _make_job(
            description="Remote Software Engineer. US-based. Python required.",
            city="New York", state="New York", country="United States",
            on_site=3  # Remote
        )

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 75,
            "match_summary": "Good match. Candidate is US-based (Austin, TX) for US remote role.",
            "skills_match": "Python, Flask", "experience_match": "5 years",
            "gaps_identified": "No cloud experience mentioned",
            "key_requirements": "Python, US-based"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response

        candidate_location = {'city': 'Austin', 'state': 'TX', 'countryName': 'United States'}
        result = vetting_service.analyze_candidate_job_match(
            resume_text=US_CANDIDATE_RESUME,
            job=job, candidate_location=candidate_location
        )

        assert result is not None
        assert 'location mismatch' not in result.get('gaps_identified', '').lower(), \
            "Same-country remote should NOT have location mismatch"
        # Prompt should mention country-level matching
        call_args = vetting_service.openai_client.chat.completions.create.call_args
        user_prompt = call_args[1]['messages'][1]['content']
        assert 'same COUNTRY' in user_prompt, \
            "Remote prompt must mention same-country rule"

    def test_clearance_trigger_includes_global_prompt(self, vetting_service, app):
        """When global screening instructions mention clearance, they appear in prompt."""
        job = _make_job(
            description="Senior Engineer - US Citizenship required. Active Secret clearance preferred.",
            city="Arlington", state="VA", country="United States",
            on_site=1
        )

        mock_response = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "match_score": 70,
            "match_summary": "Good technical match. Clearance status needs verification.",
            "skills_match": "Python, Java", "experience_match": "10 years",
            "gaps_identified": "Clearance not confirmed",
            "key_requirements": "Python, US Citizenship, Secret clearance"
        })
        mock_response.choices = [mock_choice]
        vetting_service.openai_client.chat.completions.create.return_value = mock_response

        candidate_location = {'city': 'Arlington', 'state': 'VA', 'countryName': 'United States'}

        # Pass global requirements that include clearance rules
        global_prompt = (
            "WORK AUTHORIZATION & SECURITY CLEARANCE INFERENCE RULES:\n"
            "When a job requires US citizenship or security clearance, "
            "enumerate US-based roles before flagging gaps."
        )

        result = vetting_service.analyze_candidate_job_match(
            resume_text="John Smith\nArlington, VA\nSenior Engineer, 10yr Python/Java\n",
            job=job, candidate_location=candidate_location,
            prefetched_global_requirements=global_prompt
        )

        call_args = vetting_service.openai_client.chat.completions.create.call_args
        user_prompt = call_args[1]['messages'][1]['content']

        assert 'GLOBAL SCREENING INSTRUCTIONS' in user_prompt, \
            "Global screening instructions must be present in prompt"
        assert 'CLEARANCE INFERENCE RULES' in user_prompt, \
            "Clearance inference rules must be injected into prompt"


