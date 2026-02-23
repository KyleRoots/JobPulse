"""
Tests for years-of-experience enforcement in candidate vetting.

Tests the post-processing hard gate that enforces score caps based on
years_analysis returned by the AI, as well as edge/fail-safe cases.
"""
import json
import pytest
from unittest.mock import MagicMock, patch


class TestYearsOfExperienceHardGate:
    """Test the post-processing years-of-experience hard gate logic.
    
    The hard gate runs AFTER the AI returns a score and years_analysis,
    and enforces:
      - 2+ year shortfall on any skill → cap score at 60
      - 1-2 year shortfall → reduce score by 15
      - Appends CRITICAL messages to gaps_identified
    """
    
    @pytest.fixture
    def service(self):
        """Create a CandidateVettingService with mocked dependencies."""
        with patch.dict('os.environ', {
            'OPENAI_API_KEY': 'test-key',
            'DATABASE_URL': 'sqlite:///:memory:'
        }):
            # Mock the app context and database imports
            mock_app = MagicMock()
            mock_db = MagicMock()
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

    def _make_ai_response(self, match_score, years_analysis=None, gaps=""):
        """Build a mock AI JSON response."""
        resp = {
            "match_score": match_score,
            "match_summary": "Test summary",
            "skills_match": "Python, React",
            "experience_match": "2 years at Acme Corp",
            "gaps_identified": gaps,
            "key_requirements": "• Python 3+ years\n• React 2+ years",
        }
        if years_analysis is not None:
            resp["years_analysis"] = years_analysis
        return resp
    
    def _run_hard_gate(self, service, ai_response, job_id=123):
        """Run the analyze_candidate_job_match method with a mocked OpenAI response.
        
        We mock the OpenAI call to return our controlled JSON, then the method's
        post-processing hard gate runs on the result.
        """
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = json.dumps(ai_response)
        service.openai_client.chat.completions.create.return_value = mock_completion
        
        job = {
            'id': job_id,
            'title': 'Senior Developer',
            'description': 'Need 3+ years Python, 5+ years Java',
            'address': {'city': 'Dallas', 'state': 'TX', 'countryName': 'United States'},
            'onSite': 1
        }
        
        # We need to mock _get_job_custom_requirements since it needs DB access
        with patch.object(service, '_get_job_custom_requirements', return_value=None):
            result = service.analyze_candidate_job_match(
                resume_text="Sample resume text",
                job=job,
                prefetched_requirements="• 3+ years Python\n• 5+ years Java"
            )
        
        return result
    
    # ── Test: Insufficient years (≥2yr shortfall) → score capped at 60 ──
    
    def test_large_shortfall_caps_score_at_60(self, service):
        """Candidate with 6 months vs 3+ years required → score capped at 60."""
        ai_response = self._make_ai_response(
            match_score=85,
            years_analysis={
                "Python": {
                    "required_years": 3,
                    "estimated_years": 0.5,
                    "meets_requirement": False
                }
            }
        )
        
        result = self._run_hard_gate(service, ai_response)
        
        assert result['match_score'] <= 60, \
            f"Score should be capped at 60 for 2.5yr shortfall, got {result['match_score']}"
        assert "CRITICAL: Python requires 3yr" in result['gaps_identified']

    def test_multiple_skills_worst_shortfall_applies(self, service):
        """Multiple skills with shortfalls — worst one determines the cap."""
        ai_response = self._make_ai_response(
            match_score=90,
            years_analysis={
                "Python": {
                    "required_years": 3,
                    "estimated_years": 2.0,
                    "meets_requirement": False
                },
                "Java": {
                    "required_years": 5,
                    "estimated_years": 1.0,
                    "meets_requirement": False
                }
            }
        )
        
        result = self._run_hard_gate(service, ai_response)
        
        # Java shortfall is 4yr (≥2), so hard cap applies
        assert result['match_score'] <= 60
        assert "CRITICAL: Java requires 5yr" in result['gaps_identified']
        assert "CRITICAL: Python requires 3yr" in result['gaps_identified']

    # ── Test: Meets/exceeds years → no cap applied ──
    
    def test_meets_all_years_no_cap(self, service):
        """Candidate meets all year requirements → score unchanged."""
        ai_response = self._make_ai_response(
            match_score=88,
            years_analysis={
                "Python": {
                    "required_years": 3,
                    "estimated_years": 4.5,
                    "meets_requirement": True
                },
                "React": {
                    "required_years": 2,
                    "estimated_years": 3.0,
                    "meets_requirement": True
                }
            }
        )
        
        result = self._run_hard_gate(service, ai_response)
        
        assert result['match_score'] == 88, \
            f"Score should remain 88 when years are met, got {result['match_score']}"
        # No CRITICAL messages should be in gaps
        assert "CRITICAL:" not in result.get('gaps_identified', '')

    # ── Test: Borderline case (1-2yr shortfall) → 15-point penalty ──
    
    def test_borderline_shortfall_reduces_by_15(self, service):
        """Candidate 1.5yr short on one skill → score reduced by 15."""
        ai_response = self._make_ai_response(
            match_score=82,
            years_analysis={
                "Python": {
                    "required_years": 3,
                    "estimated_years": 1.5,
                    "meets_requirement": False
                }
            }
        )
        
        result = self._run_hard_gate(service, ai_response)
        
        # 1.5yr shortfall → reduce by 15: 82 - 15 = 67
        assert result['match_score'] == 67, \
            f"Score should be 67 (82-15) for 1.5yr shortfall, got {result['match_score']}"
        assert "CRITICAL: Python requires 3yr" in result['gaps_identified']

    def test_borderline_exactly_1yr_shortfall(self, service):
        """Exactly 1yr shortfall → should still apply 15-point penalty."""
        ai_response = self._make_ai_response(
            match_score=80,
            years_analysis={
                "React": {
                    "required_years": 3,
                    "estimated_years": 2.0,
                    "meets_requirement": False
                }
            }
        )
        
        result = self._run_hard_gate(service, ai_response)
        
        # 1.0yr shortfall → reduce by 15: 80 - 15 = 65
        assert result['match_score'] == 65, \
            f"Score should be 65 (80-15) for 1.0yr shortfall, got {result['match_score']}"

    # ── Test: Missing years_analysis → no crash, fail-safe ──
    
    def test_missing_years_analysis_no_crash(self, service):
        """If AI omits years_analysis entirely, score passes through unchanged."""
        ai_response = self._make_ai_response(
            match_score=85,
            years_analysis=None  # Not included in response
        )
        # Remove years_analysis key entirely  
        ai_response.pop('years_analysis', None)
        
        result = self._run_hard_gate(service, ai_response)
        
        assert result['match_score'] == 85, \
            f"Score should be unchanged when years_analysis missing, got {result['match_score']}"

    def test_empty_years_analysis_no_crash(self, service):
        """Empty years_analysis dict → score unchanged."""
        ai_response = self._make_ai_response(
            match_score=85,
            years_analysis={}
        )
        
        result = self._run_hard_gate(service, ai_response)
        
        assert result['match_score'] == 85, \
            f"Score should be unchanged for empty years_analysis, got {result['match_score']}"

    def test_malformed_years_analysis_no_crash(self, service):
        """Malformed years_analysis (not a dict) → no crash."""
        ai_response = self._make_ai_response(
            match_score=85,
            years_analysis="invalid string"
        )
        
        result = self._run_hard_gate(service, ai_response)
        
        assert result['match_score'] == 85, \
            f"Score should be unchanged for malformed years_analysis, got {result['match_score']}"

    # ── Test: Score never goes below 0 ──
    
    def test_penalty_does_not_go_below_zero(self, service):
        """If score is already low, penalty shouldn't produce negative score."""
        ai_response = self._make_ai_response(
            match_score=10,
            years_analysis={
                "Python": {
                    "required_years": 3,
                    "estimated_years": 1.5,
                    "meets_requirement": False
                }
            }
        )
        
        result = self._run_hard_gate(service, ai_response)
        
        assert result['match_score'] >= 0, \
            f"Score should not go below 0, got {result['match_score']}"

    # ── Test: Hard gate preserves existing gaps ──
    
    def test_appends_to_existing_gaps(self, service):
        """CRITICAL message should be appended to existing gaps, not replace them."""
        ai_response = self._make_ai_response(
            match_score=85,
            years_analysis={
                "Python": {
                    "required_years": 5,
                    "estimated_years": 1.0,
                    "meets_requirement": False
                }
            },
            gaps="Missing Docker certification"
        )
        
        result = self._run_hard_gate(service, ai_response)
        
        assert "Missing Docker certification" in result['gaps_identified'], \
            "Existing gaps should be preserved"
        assert "CRITICAL: Python requires 5yr" in result['gaps_identified'], \
            "Years shortfall should be appended"

    # ── Test: Score already at or below cap ──
    
    def test_score_already_below_cap_unchanged(self, service):
        """If AI already scored ≤60 with 2+ yr shortfall, don't change it."""
        ai_response = self._make_ai_response(
            match_score=45,
            years_analysis={
                "Python": {
                    "required_years": 5,
                    "estimated_years": 0.5,
                    "meets_requirement": False
                }
            }
        )
        
        result = self._run_hard_gate(service, ai_response)
        
        # Score was already 45 which is below cap of 60
        assert result['match_score'] == 45, \
            f"Score already below cap should remain unchanged, got {result['match_score']}"
        # But CRITICAL message should still be in gaps
        assert "CRITICAL: Python requires 5yr" in result['gaps_identified']


class TestYearsAnalysisPrompt:
    """Test that the prompt includes years_analysis instructions."""
    
    @pytest.fixture
    def service(self):
        """Create a CandidateVettingService with mocked dependencies."""
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
    
    def test_prompt_contains_years_analysis_instruction(self, service):
        """The prompt sent to OpenAI should include years analysis instructions."""
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = json.dumps({
            "match_score": 70,
            "match_summary": "test",
            "skills_match": "test",
            "experience_match": "test",
            "gaps_identified": "test",
            "key_requirements": "test",
            "years_analysis": {}
        })
        service.openai_client.chat.completions.create.return_value = mock_completion
        
        job = {
            'id': 999,
            'title': 'Test Job',
            'description': 'Need 3+ years Python',
            'address': {},
            'onSite': 1
        }
        
        with patch.object(service, '_get_job_custom_requirements', return_value=None):
            service.analyze_candidate_job_match(
                resume_text="Sample resume",
                job=job,
                prefetched_requirements=None
            )
        
        # Verify the prompt sent to OpenAI
        call_args = service.openai_client.chat.completions.create.call_args
        messages = call_args.kwargs.get('messages', call_args[1].get('messages', []))
        user_prompt = messages[1]['content']
        system_prompt = messages[0]['content']
        
        # Check user prompt contains years analysis section
        assert "YEARS OF EXPERIENCE ANALYSIS (MANDATORY)" in user_prompt
        assert "years_analysis" in user_prompt
        assert "50% weight" in user_prompt
        assert "University coursework" in user_prompt
        
        # Check system prompt contains years rules
        assert "YEARS OF EXPERIENCE MATTER" in system_prompt
        assert "DISTINGUISH PROFESSIONAL VS ACADEMIC EXPERIENCE" in system_prompt


class TestYearsOfExperienceRealisticRoles:
    """Realistic role scenarios for years-of-experience hard gate.

    Uses concrete job titles and year counts that exercise the
    ≥2yr shortfall cap and 1-2yr borderline penalty tiers.
    """

    @pytest.fixture
    def service(self):
        """Create a CandidateVettingService with mocked dependencies."""
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

    def _make_ai_response(self, match_score, years_analysis=None, gaps=""):
        resp = {
            "match_score": match_score,
            "match_summary": "Test summary",
            "skills_match": "Python, ML",
            "experience_match": "Experience at Acme",
            "gaps_identified": gaps,
            "key_requirements": "• MLOps 5+ years\n• Kubernetes 3+ years",
        }
        if years_analysis is not None:
            resp["years_analysis"] = years_analysis
        return resp

    def _run_hard_gate(self, service, ai_response, job_id=200):
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock()]
        mock_completion.choices[0].message.content = json.dumps(ai_response)
        service.openai_client.chat.completions.create.return_value = mock_completion

        job = {
            'id': job_id,
            'title': 'Senior MLOps Engineer',
            'description': 'Need 5+ years MLOps, 3+ years Kubernetes',
            'address': {'city': 'Austin', 'state': 'TX', 'countryName': 'United States'},
            'onSite': 1
        }

        with patch.object(service, '_get_job_custom_requirements', return_value=None):
            return service.analyze_candidate_job_match(
                resume_text="Sample resume",
                job=job,
                prefetched_requirements="• MLOps 5+ years\n• Kubernetes 3+ years"
            )

    def test_mlops_3yr_shortfall_caps_at_60(self, service):
        """MLOps role: candidate has 2yr vs 5yr required → 3yr shortfall → cap at 60."""
        ai_response = self._make_ai_response(
            match_score=88,
            years_analysis={
                "MLOps": {
                    "required_years": 5,
                    "estimated_years": 2.0,
                    "meets_requirement": False
                }
            }
        )

        result = self._run_hard_gate(service, ai_response)

        assert result['match_score'] <= 60, \
            f"Score should be capped at 60 for 3yr MLOps shortfall, got {result['match_score']}"
        assert "CRITICAL" in result.get('gaps_identified', '')

    def test_data_architect_8yr_borderline_penalty(self, service):
        """Data Architect: candidate has 7yr vs 8yr required → 1yr shortfall → -15 penalty."""
        ai_response = self._make_ai_response(
            match_score=85,
            years_analysis={
                "Data Architecture": {
                    "required_years": 8,
                    "estimated_years": 7.0,
                    "meets_requirement": False
                }
            }
        )

        result = self._run_hard_gate(service, ai_response)

        # 1yr shortfall → -15 penalty → 85 - 15 = 70
        assert result['match_score'] <= 70, \
            f"Score should be ≤70 for 1yr borderline shortfall, got {result['match_score']}"
        # Should NOT be capped at 60 (shortfall < 2yr)
        assert result['match_score'] > 55, \
            f"Score should be >55 (borderline, not hard cap), got {result['match_score']}"

    def test_senior_role_meets_requirements_no_cap(self, service):
        """Candidate meets all year requirements → no penalty applied."""
        ai_response = self._make_ai_response(
            match_score=91,
            years_analysis={
                "MLOps": {
                    "required_years": 5,
                    "estimated_years": 7.0,
                    "meets_requirement": True
                },
                "Kubernetes": {
                    "required_years": 3,
                    "estimated_years": 4.5,
                    "meets_requirement": True
                }
            }
        )

        result = self._run_hard_gate(service, ai_response)

        assert result['match_score'] == 91, \
            f"Score should be unchanged at 91 (meets all requirements), got {result['match_score']}"

