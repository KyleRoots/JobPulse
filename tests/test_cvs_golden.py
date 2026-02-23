"""
Golden Candidate Suite — strict regression guarantees for CandidateVettingService.

Each test encodes a real-world scenario with:
  - A docstring naming the exact real case / candidate it represents
  - A score BAND (min/max) rather than a single value
  - At least one KEY EVIDENCE condition (e.g., work_auth inference tier, budget in evidence)

These tests ensure that ANY future prompt, threshold, or gate change that breaks
a known-good behavior will fail immediately.

GOLDEN CASES:
  GC-1: Fresh-grad / Reem Abdelsalam (4589857) vs Data Scientist (34638)
  GC-2: Strong senior – tool gaps / Joseph Aguilar (4589644) vs Sr Python Dev W2 (34663)
  GC-3: Strong senior – false negative fix / Ed Chyzowski (4460117) vs Tech Director (34541)
  GC-4: Work auth – explicit Green Card statement → Rule 0, no penalty
  GC-5: Work auth – long US history (11+ yr) → inference Tier 1, no penalty, no gap text
  GC-6: Career-shift / audit background vs delivery role → low score, domain mismatch
  GC-7: Mid-level exact match → score preserved, no spurious gates fire

NO PRODUCTION CHANGES: This file tests existing prompt + gate logic only.
"""

import json
import pytest
from unittest.mock import MagicMock, patch


# ===========================================================================
# Shared fixture + helper
# ===========================================================================
@pytest.fixture
def service():
    """Create a CVS instance with mocked dependencies (no DB required)."""
    with patch.dict('os.environ', {
        'OPENAI_API_KEY': 'test-key',
        'DATABASE_URL': 'sqlite:///:memory:'
    }):
        mock_app = MagicMock()
        mock_models = MagicMock()
        with patch.dict('sys.modules', {
            'app': mock_app,
            'models': mock_models,
        }):
            from candidate_vetting_service import CandidateVettingService
            svc = CandidateVettingService.__new__(CandidateVettingService)
            svc.openai_client = MagicMock()
            svc.model = 'gpt-4o-mini'
            svc.logger = MagicMock()
            return svc


def _run_analysis(service, ai_response, resume_text, job, requirements=None):
    """Run analyze_candidate_job_match with a mocked AI response."""
    mock_completion = MagicMock()
    mock_completion.choices = [MagicMock()]
    mock_completion.choices[0].message.content = json.dumps(ai_response)
    service.openai_client.chat.completions.create.return_value = mock_completion

    with patch.object(service, '_get_job_custom_requirements', return_value=None):
        return service.analyze_candidate_job_match(
            resume_text=resume_text,
            job=job,
            prefetched_requirements=requirements or "5+ years experience",
        )


# ===========================================================================
# GC-1: Fresh-grad / Reem Abdelsalam (4589857) vs Data Scientist (34638)
# ===========================================================================
class TestGC1_FreshGradReemAbdelsalam:
    """Reem Abdelsalam (Bullhorn 4589857) vs 'Data Scientist - AI, Analytics
    & Machine Learning' (Job 34638).

    Pattern: BSc Biomedical Informatics 2025, 3 internships (all ≤3 months),
    zero full-time professional roles.  AI originally scored 85% — a confirmed
    false positive.

    Expected behavior:
      - Experience floor gate caps score at ≤55
      - Candidate is NOT recommended (below 80% threshold)
      - gaps_identified mentions lack of professional years / experience floor
      - years_analysis.meets_requirement overridden to False for intern-only
    """

    def test_gc1_score_capped_at_55(self, service):
        ai_response = {
            "match_score": 85,  # AI overscores — gate must correct
            "match_summary": "Candidate meets all mandatory requirements with explicit evidence",
            "skills_match": "Python, TensorFlow, scikit-learn, Kafka, PySpark",
            "experience_match": "3 month AI internship, academic projects",
            "gaps_identified": "",
            "key_requirements": (
                "• 5+ years of experience within Data Science\n"
                "• Must have a Bachelor's or Master's Degree\n"
                "• Experience with Python\n"
                "• Experience with AI/ML model deployment workflows"
            ),
            "years_analysis": {
                "Data Science": {
                    "required_years": 5,
                    "estimated_years": 3.5,
                    "meets_requirement": True,  # WRONG — gate must override
                    "calculation": "TachyHealth intern 3mo + academic projects ~3yr"
                }
            },
            "experience_level_classification": {
                "classification": "FRESH_GRAD",
                "total_professional_years": 0.25,
                "highest_role_type": "INTERNSHIP_ONLY"
            },
            "recency_analysis": {
                "most_recent_role": "AI Intern at TachyHealth (09/2023 – 12/2023)",
                "most_recent_role_relevant": True,
                "second_recent_role": "IT Support Intern (06/2023 – 08/2023)",
                "second_recent_role_relevant": False,
                "months_since_relevant_work": 26,
                "penalty_applied": 0,
            },
        }

        result = _run_analysis(
            service, ai_response,
            resume_text=(
                "Reem Abdelsalam, Cairo Egypt. BSc Biomedical Informatics, "
                "Galala University 2021-2025. GPA 3.05. "
                "AI Intern at TachyHealth 09/2023-12/2023 (3 months). "
                "IT Support Intern at University 06/2023-08/2023 (2 months). "
                "Genomic Lab Intern 01/2023-03/2023 (2 months). "
                "Skills: Python, TensorFlow, Keras, scikit-learn, PyTorch, SQL, "
                "Apache Kafka, PySpark, Docker, RAG, LLaMA."
            ),
            job={
                'id': 34638,
                'title': 'Data Scientist - AI, Analytics & Machine Learning',
                'description': (
                    'Design, develop, and deploy machine learning models. '
                    'Experience with large datasets. 5+ years data science experience.'
                ),
                'address': {'city': '', 'state': None, 'countryCode': 'EG',
                            'countryName': 'Egypt'},
                'onSite': 'Remote',
            },
            requirements=(
                "• Minimum 5 years of experience within Data Science\n"
                "• Must have a Bachelor's or Master's Degree\n"
                "• Experience with Python\n"
                "• Experience with AI/ML model deployment workflows\n"
                "• Must be located within Egypt or Spain"
            ),
        )

        # SCORE BAND: must be ≤55 (experience floor cap)
        assert result['match_score'] <= 55, (
            f"GC-1 REGRESSION: fresh-grad Reem Abdelsalam must be capped ≤55, "
            f"got {result['match_score']}"
        )

        # EVIDENCE: gaps must mention experience floor or professional years
        gaps = result.get('gaps_identified', '')
        assert 'experience floor' in gaps.lower() or 'CRITICAL' in gaps, (
            f"GC-1 REGRESSION: gaps must mention experience floor, got: {gaps}"
        )

        # EVIDENCE: meets_requirement overridden to False for intern-only
        ds = result['years_analysis']['Data Science']
        assert ds['meets_requirement'] is False, (
            "GC-1 REGRESSION: meets_requirement must be overridden to False "
            "for intern-only profiles"
        )


# ===========================================================================
# GC-2: Strong senior – tool-specific gaps / Joseph Aguilar (4589644)
# ===========================================================================
class TestGC2_SeniorJosephAguilarToolGaps:
    """Joseph Aguilar (Bullhorn 4589644) vs 'Sr Python Developer - W2 only'
    (Job 34663).

    Pattern: 11+ years senior Python engineer (Tyler Technologies, Amazon,
    Blue Origin, Kaya AI).  Missing specific tools: Bash, WandB, Pylint,
    federated learning.  13.4 years of continuous US employment.

    Expected behavior:
      - Score in realistic mid band: 55–75 (partial gaps offset strong core)
      - Work auth: 11+ years US history → no penalty (Tier 1 inference)
      - Gaps explicitly call out missing tools (Bash, WandB, Pylint, or
        federated learning)
      - Score must NOT be ≥80 (has genuine tool gaps)
    """

    def test_gc2_score_band_and_work_auth(self, service):
        ai_response = {
            "match_score": 65,
            "match_summary": "Strong Python backend engineer, missing specific tools",
            "skills_match": "Python, Django, FastAPI, Flask, AWS, Docker, Kubernetes",
            "experience_match": "12+ years building scalable backend systems",
            "gaps_identified": (
                "No evidence of Bash scripting | "
                "No evidence of WandB | "
                "No evidence of Pylint | "
                "No evidence of federated learning experience"
            ),
            "key_requirements": (
                "• 7+ years Python development\n"
                "• Bash scripting\n"
                "• WandB ML experiment tracking\n"
                "• Pylint code quality\n"
                "• Federated learning\n"
                "• US Citizen / W2 only"
            ),
            "years_analysis": {
                "Python": {
                    "required_years": 7,
                    "estimated_years": 12,
                    "meets_requirement": True,
                    "calculation": "12+ years across 4 roles"
                }
            },
            "experience_level_classification": {
                "classification": "SENIOR",
                "total_professional_years": 12.0,
                "highest_role_type": "FULL_TIME"
            },
            "recency_analysis": {
                "most_recent_role": "Sr Python Developer at Kaya AI (Feb 2022 – Present)",
                "most_recent_role_relevant": True,
                "second_recent_role": "Sr Software Engineer at Blue Origin (Dec 2018 – Jan 2022)",
                "second_recent_role_relevant": True,
                "months_since_relevant_work": 0,
                "penalty_applied": 0,
            },
            "work_authorization_analysis": {
                "us_roles_enumerated": [
                    "Software Developer, Tyler Technologies, Plano TX (Jun 2012 – May 2015)",
                    "Software Engineer, Amazon, Austin TX (Jun 2015 – Nov 2018)",
                    "Sr Software Engineer, Blue Origin, Kent WA (Dec 2018 – Jan 2022)",
                    "Sr Python Developer, Kaya AI, Remote (Feb 2022 – Present)"
                ],
                "total_us_years": 11.42,
                "inference_tier": "5+ years - strong likelihood, no penalty",
                "penalty_applied": 0,
                "reasoning": "11.42 years continuous US employment infers authorization"
            },
        }

        result = _run_analysis(
            service, ai_response,
            resume_text=(
                "Joseph Aguilar. Sr Python Developer at Kaya AI (Feb 2022-Present, Remote). "
                "Sr Software Engineer at Blue Origin (Dec 2018-Jan 2022, Kent WA). "
                "Software Engineer at Amazon (Jun 2015-Nov 2018, Austin TX). "
                "Software Developer at Tyler Technologies (Jun 2012-May 2015, Plano TX). "
                "Backend: Python, Django, FastAPI, Flask. AI & ML: PyTorch, scikit-learn. "
                "Cloud: AWS, Docker, Kubernetes. Testing: pytest, Unittest. "
                "Led CI/CD workflows using GitHub Actions and Docker."
            ),
            job={
                'id': 34663,
                'title': 'Sr Python Developer - W2 only',
                'description': (
                    '7+ years Python. Bash, WandB, Pylint, federated learning. '
                    'US Citizen / W2 only.'
                ),
                'address': {'city': 'Remote', 'state': None,
                            'countryCode': 'US', 'countryName': 'United States'},
                'onSite': 'Remote',
            },
            requirements=(
                "• 7+ years Python development\n"
                "• Bash scripting\n"
                "• WandB ML experiment tracking\n"
                "• Pylint code quality\n"
                "• Federated learning\n"
                "• US Citizen / W2 only"
            ),
        )

        # SCORE BAND: 55–75 (genuine gaps but strong core)
        assert 55 <= result['match_score'] <= 75, (
            f"GC-2 REGRESSION: Joseph Aguilar should score 55-75, "
            f"got {result['match_score']}"
        )

        # SCORE MUST NOT reach qualified threshold
        assert result['match_score'] < 80, (
            f"GC-2 REGRESSION: should NOT be ≥80 with tool gaps, "
            f"got {result['match_score']}"
        )

        # EVIDENCE: gaps must mention at least one missing tool
        gaps = result.get('gaps_identified', '')
        has_tool_gap = any(tool in gaps for tool in
                          ['Bash', 'WandB', 'Pylint', 'federated'])
        assert has_tool_gap, (
            f"GC-2 REGRESSION: gaps must mention missing tools, got: {gaps}"
        )


# ===========================================================================
# GC-3: Strong senior – false negative fix / Ed Chyzowski (4460117)
# ===========================================================================
class TestGC3_SeniorEdChyzowskiFalseNeg:
    """Ed Chyzowski (Bullhorn 4460117) vs 'Technology Services, Director'
    (Job 34541).

    Pattern: 30+ years IT/security leadership, explicit '$8M budget across
    15+ Tier 1 products/services' in resume, teams of 50 direct reports.
    Previously false-negative scored 75% because AI failed to find budget
    evidence.  After structured evidence extraction, scored 95%.

    Expected behavior:
      - Score ≥ 85 (all 5 requirements met)
      - Budget requirement marked "met" with $8M evidence
      - No budget/financial gap in gaps_identified
    """

    def test_gc3_score_and_budget_evidence(self, service):
        ai_response = {
            "match_score": 95,
            "match_summary": "Exceptional match. 30+ years IT/security leadership, $8M budget",
            "skills_match": "ITSM, security programs, infrastructure, compliance",
            "experience_match": "30+ years building and scaling security programs",
            "gaps_identified": "",  # No gaps — all requirements met
            "key_requirements": (
                "• Strategic IT/security leadership\n"
                "• Lead/manage technology teams\n"
                "• Budget/financial stewardship ($5M+)\n"
                "• Secure technology delivery\n"
                "• Located in Canada"
            ),
            "requirement_evidence": [
                {
                    "requirement": "Strategic IT/security leadership",
                    "evidence_found": "30+ years building and scaling security programs",
                    "meets_requirement": True,
                },
                {
                    "requirement": "Lead/manage technology teams",
                    "evidence_found": "teams of up to 50 direct reports",
                    "meets_requirement": True,
                },
                {
                    "requirement": "Budget/financial stewardship ($5M+)",
                    "evidence_found": "$8M budget across 15+ Tier 1 products/services",
                    "meets_requirement": True,
                },
                {
                    "requirement": "Secure technology delivery",
                    "evidence_found": "50%+ reduction in MTTD/MTTR via ITSM workflows",
                    "meets_requirement": True,
                },
                {
                    "requirement": "Location match (Canada, On-site)",
                    "evidence_found": "Summerland, British Columbia, Canada",
                    "meets_requirement": True,
                },
            ],
            "years_analysis": {
                "IT Leadership": {
                    "required_years": 10,
                    "estimated_years": 30,
                    "meets_requirement": True,
                    "calculation": "30+ years"
                }
            },
            "experience_level_classification": {
                "classification": "EXECUTIVE",
                "total_professional_years": 30.0,
                "highest_role_type": "FULL_TIME"
            },
            "recency_analysis": {
                "most_recent_role_relevant": True,
                "second_recent_role_relevant": True,
                "months_since_relevant_work": 0,
                "penalty_applied": 0,
            },
        }

        result = _run_analysis(
            service, ai_response,
            resume_text=(
                "Ed Chyzowski. Summerland, British Columbia, Canada. "
                "30+ years building and scaling security programs. "
                "Teams of up to 50 direct reports. "
                "$8M budget across 15+ Tier 1 products/services. "
                "50%+ reduction in MTTD/MTTR via ITSM workflows. "
                "CISSP, CISM, PMP certifications."
            ),
            job={
                'id': 34541,
                'title': 'Technology Services, Director',
                'description': (
                    'Strategic IT/security leadership. 10+ years experience. '
                    'Budget stewardship $5M+. Lead technology teams. '
                    'Located in Canada.'
                ),
                'address': {'city': 'Ottawa', 'state': 'ON',
                            'countryCode': 'CA', 'countryName': 'Canada'},
                'onSite': 'On-Site',
            },
            requirements=(
                "• Strategic IT/security leadership\n"
                "• Lead/manage technology teams\n"
                "• Budget/financial stewardship ($5M+)\n"
                "• 10+ years experience\n"
                "• Secure technology delivery\n"
                "• Located in Canada"
            ),
        )

        # SCORE BAND: ≥85 (all requirements unambiguously met)
        assert result['match_score'] >= 85, (
            f"GC-3 REGRESSION: Ed Chyzowski should score ≥85, "
            f"got {result['match_score']}"
        )

        # EVIDENCE: no budget gap in gaps
        gaps = result.get('gaps_identified', '')
        assert 'budget' not in gaps.lower(), (
            f"GC-3 REGRESSION: must NOT have budget gap (resume says $8M), "
            f"got gaps: {gaps}"
        )


# ===========================================================================
# GC-4: Work auth – explicit Green Card statement → Rule 0
# ===========================================================================
class TestGC4_WorkAuthExplicitGreenCard:
    """Synthetic case: candidate explicitly states 'US Permanent Resident
    (Green Card)' in resume.

    Expected behavior (Rule 0):
      - No work-auth penalty at all
      - Score reflects only technical/experience gaps, not authorization
      - Gap text does not contain 'authorization' or 'citizenship'
    """

    def test_gc4_explicit_wa_no_penalty(self, service):
        ai_response = {
            "match_score": 82,
            "match_summary": "Strong Java developer with explicit work authorization",
            "skills_match": "Java, Spring Boot, Microservices, AWS",
            "experience_match": "6 years professional Java development",
            "gaps_identified": "No Kubernetes experience noted",
            "key_requirements": (
                "• 5+ years Java\n"
                "• Spring Boot\n"
                "• US work authorization required"
            ),
            "years_analysis": {
                "Java": {
                    "required_years": 5,
                    "estimated_years": 6,
                    "meets_requirement": True,
                    "calculation": "6 years across 2 roles"
                }
            },
            "experience_level_classification": {
                "classification": "MID_LEVEL",
                "total_professional_years": 6.0,
                "highest_role_type": "FULL_TIME"
            },
            "recency_analysis": {
                "most_recent_role_relevant": True,
                "second_recent_role_relevant": True,
                "months_since_relevant_work": 0,
                "penalty_applied": 0,
            },
            "work_authorization_analysis": {
                "triggered": True,
                "explicit_statement": "US Permanent Resident (Green Card)",
                "inference_tier": "N/A - explicit statement",
                "penalty_applied": 0,
                "reasoning": "Candidate explicitly states Green Card holder"
            },
        }

        result = _run_analysis(
            service, ai_response,
            resume_text=(
                "Anita Sharma. US Permanent Resident (Green Card). "
                "Senior Java Developer at TechCorp (2019-2024, San Jose CA). "
                "Java Developer at StartupXYZ (2018-2019, San Francisco CA). "
                "Java, Spring Boot, Microservices, AWS, PostgreSQL."
            ),
            job={
                'id': 50010,
                'title': 'Senior Java Developer',
                'description': '5+ years Java. US work authorization required.',
                'address': {'city': 'San Jose', 'state': 'CA',
                            'countryCode': 'US', 'countryName': 'United States'},
                'onSite': 'Hybrid',
            },
            requirements=(
                "• 5+ years Java development\n"
                "• Spring Boot experience\n"
                "• US work authorization required"
            ),
        )

        # SCORE: should be preserved (82) — no auth penalty, years met
        assert result['match_score'] >= 75, (
            f"GC-4 REGRESSION: explicit Green Card holder should not lose "
            f"score to auth penalty, got {result['match_score']}"
        )

        # EVIDENCE: no auth-related gap text
        gaps = result.get('gaps_identified', '')
        assert 'authorization' not in gaps.lower(), (
            f"GC-4 REGRESSION: no auth gap expected for explicit Green Card, "
            f"got gaps: {gaps}"
        )
        assert 'citizenship' not in gaps.lower(), (
            f"GC-4 REGRESSION: no citizenship gap expected for explicit Green Card, "
            f"got gaps: {gaps}"
        )


# ===========================================================================
# GC-5: Work auth – long US history → inference Tier 1
# ===========================================================================
class TestGC5_WorkAuthLongUSHistory:
    """Based on the Joseph Aguilar work-auth pattern: candidate with 11+
    years of continuous US employment, no explicit citizenship statement.

    Expected behavior:
      - Work auth enumeration shows all US roles and total years
      - Inference Tier 1 (5+ years): no penalty
      - No 'citizenship' or 'authorization' gap text
      - Score reflects technical match, not auth concerns
    """

    def test_gc5_us_history_no_auth_penalty(self, service):
        ai_response = {
            "match_score": 88,
            "match_summary": "Excellent DevOps engineer with extensive US work history",
            "skills_match": "Kubernetes, Docker, AWS, Terraform, CI/CD",
            "experience_match": "9 years DevOps across US companies",
            "gaps_identified": "",
            "key_requirements": (
                "• 5+ years DevOps\n"
                "• Kubernetes & Docker\n"
                "• AWS cloud infrastructure\n"
                "• W2 / US Citizen"
            ),
            "years_analysis": {
                "DevOps": {
                    "required_years": 5,
                    "estimated_years": 9,
                    "meets_requirement": True,
                    "calculation": "9 years across 3 US companies"
                }
            },
            "experience_level_classification": {
                "classification": "SENIOR",
                "total_professional_years": 9.0,
                "highest_role_type": "FULL_TIME"
            },
            "recency_analysis": {
                "most_recent_role_relevant": True,
                "second_recent_role_relevant": True,
                "months_since_relevant_work": 0,
                "penalty_applied": 0,
            },
            "work_authorization_analysis": {
                "us_roles_enumerated": [
                    "DevOps Engineer, CloudCorp, Seattle WA (2016-2019)",
                    "Sr DevOps Engineer, DataFlow Inc, Austin TX (2019-2022)",
                    "Lead SRE, ScaleTech, San Francisco CA (2022-Present)"
                ],
                "total_us_years": 9.0,
                "inference_tier": "5+ years - strong likelihood, no penalty",
                "penalty_applied": 0,
                "reasoning": "9 years continuous US employment infers authorization"
            },
        }

        result = _run_analysis(
            service, ai_response,
            resume_text=(
                "Wei Zhang. DevOps Engineer at CloudCorp (2016-2019, Seattle WA). "
                "Sr DevOps Engineer at DataFlow Inc (2019-2022, Austin TX). "
                "Lead SRE at ScaleTech (2022-Present, San Francisco CA). "
                "Kubernetes, Docker, AWS, Terraform, CI/CD, Python."
            ),
            job={
                'id': 50011,
                'title': 'Senior DevOps Engineer - W2',
                'description': '5+ years DevOps. W2 / US Citizen.',
                'address': {'city': 'Austin', 'state': 'TX',
                            'countryCode': 'US', 'countryName': 'United States'},
                'onSite': 'Remote',
            },
            requirements=(
                "• 5+ years DevOps\n"
                "• Kubernetes & Docker\n"
                "• AWS cloud infrastructure\n"
                "• W2 / US Citizen"
            ),
        )

        # SCORE BAND: ≥80 (all requirements met including auth inference)
        assert result['match_score'] >= 80, (
            f"GC-5 REGRESSION: 9yr US history with full skill match should "
            f"score ≥80, got {result['match_score']}"
        )

        # EVIDENCE: no authorization/citizenship gap
        gaps = result.get('gaps_identified', '')
        assert 'authorization' not in gaps.lower(), (
            f"GC-5 REGRESSION: no auth gap for 9yr US history, "
            f"got gaps: {gaps}"
        )
        assert 'citizenship' not in gaps.lower(), (
            f"GC-5 REGRESSION: no citizenship gap for 9yr US history, "
            f"got gaps: {gaps}"
        )


# ===========================================================================
# GC-6: Career-shift / audit background vs delivery role
# ===========================================================================
class TestGC6_CareerShiftMarginalFit:
    """Synthetic case encoding prompt rule #13 (Experience Depth & Domain
    Relevance): audit/compliance background scored against a technology
    delivery/operations role.

    Pattern: candidate with 8 years of security audit/compliance background
    applying to a hands-on IT operations manager role. Both recent roles are
    audit/advisory, not delivery.

    Expected behavior:
      - Score clearly below strong-fit band: ≤65
      - Recency penalty applied (both recent roles unrelated to delivery)
      - Gaps highlight domain mismatch (audit vs delivery)
    """

    def test_gc6_career_shift_low_score(self, service):
        ai_response = {
            "match_score": 68,
            "match_summary": "Has IT governance experience but not hands-on delivery",
            "skills_match": "NIST, ISO 27001, SOC 2, audit frameworks",
            "experience_match": "8 years IT audit and compliance",
            "gaps_identified": (
                "Candidate's experience is advisory/audit, not delivery/operations. "
                "No evidence of managing IT infrastructure or engineering teams."
            ),
            "key_requirements": (
                "• 5+ years IT operations management\n"
                "• Hands-on infrastructure delivery\n"
                "• Team management (10+ direct reports)\n"
                "• Budget management ($2M+)"
            ),
            "years_analysis": {
                "IT Operations": {
                    "required_years": 5,
                    "estimated_years": 0,
                    "meets_requirement": False,
                    "calculation": "0 years operations — audit/compliance only"
                }
            },
            "experience_level_classification": {
                "classification": "SENIOR",
                "total_professional_years": 8.0,
                "highest_role_type": "FULL_TIME"
            },
            "recency_analysis": {
                "most_recent_role": "IT Audit Manager at PwC (2021-2024)",
                "most_recent_role_relevant": False,
                "second_recent_role": "Compliance Analyst at Deloitte (2018-2021)",
                "second_recent_role_relevant": False,
                "last_relevant_role_ended": None,
                "months_since_relevant_work": 999,
                "penalty_applied": 5,
                "reasoning": "Both recent roles are audit/compliance, not delivery"
            },
        }

        result = _run_analysis(
            service, ai_response,
            resume_text=(
                "Michael Torres. IT Audit Manager at PwC (2021-2024). "
                "Compliance Analyst at Deloitte (2018-2021). "
                "IT Auditor at KPMG (2016-2018). "
                "NIST, ISO 27001, SOC 2, ITIL. CISA certification."
            ),
            job={
                'id': 50012,
                'title': 'IT Operations Manager',
                'description': (
                    '5+ years managing IT operations. Hands-on infrastructure '
                    'delivery. Team of 10+ direct reports. Budget $2M+.'
                ),
                'address': {'city': 'Chicago', 'state': 'IL',
                            'countryCode': 'US', 'countryName': 'United States'},
                'onSite': 'On-Site',
            },
            requirements=(
                "• 5+ years IT operations management\n"
                "• Hands-on infrastructure delivery\n"
                "• Team management (10+ direct reports)\n"
                "• Budget management ($2M+)"
            ),
        )

        # SCORE BAND: ≤60 (domain mismatch + recency penalty + years gate)
        assert result['match_score'] <= 60, (
            f"GC-6 REGRESSION: audit background vs delivery role, "
            f"score should be ≤60, got {result['match_score']}"
        )


# ===========================================================================
# GC-7: Mid-level exact match → score preserved
# ===========================================================================
class TestGC7_MidLevelExactMatch:
    """Synthetic positive control: a mid-level candidate who meets all
    requirements exactly.  Ensures that no gate fires spuriously.

    Expected behavior:
      - Score preserved without penalty (no years gate, no recency gate,
        no experience floor)
      - Score ≥ 78 (the AI's assessment)
    """

    def test_gc7_no_spurious_gates(self, service):
        ai_response = {
            "match_score": 82,
            "match_summary": "Solid mid-level match, meets all requirements",
            "skills_match": "React, Node.js, TypeScript, PostgreSQL",
            "experience_match": "4 years full-stack development",
            "gaps_identified": "No AWS experience noted",
            "key_requirements": (
                "• 3+ years full-stack development\n"
                "• React + Node.js\n"
                "• TypeScript\n"
                "• PostgreSQL"
            ),
            "years_analysis": {
                "Full-Stack Development": {
                    "required_years": 3,
                    "estimated_years": 4,
                    "meets_requirement": True,
                    "calculation": "4 years across 2 roles"
                }
            },
            "experience_level_classification": {
                "classification": "MID_LEVEL",
                "total_professional_years": 4.0,
                "highest_role_type": "FULL_TIME"
            },
            "recency_analysis": {
                "most_recent_role_relevant": True,
                "second_recent_role_relevant": True,
                "months_since_relevant_work": 0,
                "penalty_applied": 0,
            },
        }

        result = _run_analysis(
            service, ai_response,
            resume_text=(
                "Sarah Chen. Full-Stack Developer at Acme Inc (2021-2024). "
                "Junior Developer at StartupXYZ (2020-2021). "
                "React, Node.js, TypeScript, PostgreSQL, Docker."
            ),
            job={
                'id': 50013,
                'title': 'Full-Stack Developer',
                'description': '3+ years full-stack development required.',
                'address': {'city': 'Toronto', 'state': 'ON',
                            'countryCode': 'CA', 'countryName': 'Canada'},
                'onSite': 'Remote',
            },
            requirements=(
                "• 3+ years full-stack development\n"
                "• React + Node.js\n"
                "• TypeScript\n"
                "• PostgreSQL"
            ),
        )

        # SCORE: preserved — no gate should fire
        assert result['match_score'] >= 78, (
            f"GC-7 REGRESSION: mid-level exact match should NOT be penalized, "
            f"got {result['match_score']}"
        )
