"""
Tests for the embedding service (Layer 1 pre-filter).

Tests cover:
- Embedding vector generation
- Cosine similarity computation
- Description hash change detection
- Job embedding caching
- Similarity-based job filtering
- Killswitch behavior
- Threshold configuration
- EmbeddingFilterLog audit logging
- EscalationLog logging
"""

import json
import math
import os
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch, PropertyMock


class TestComputeSimilarity:
    """Tests for cosine similarity computation."""
    
    def test_identical_vectors_return_one(self):
        """Identical vectors should have similarity of 1.0."""
        from embedding_service import EmbeddingService
        
        vec = [1.0, 2.0, 3.0]
        result = EmbeddingService.compute_similarity(vec, vec)
        assert abs(result - 1.0) < 0.001
    
    def test_orthogonal_vectors_return_zero(self):
        """Orthogonal vectors should have similarity of 0.0."""
        from embedding_service import EmbeddingService
        
        vec_a = [1.0, 0.0, 0.0]
        vec_b = [0.0, 1.0, 0.0]
        result = EmbeddingService.compute_similarity(vec_a, vec_b)
        assert abs(result) < 0.001
    
    def test_opposite_vectors_clamped_to_zero(self):
        """Opposite vectors should return 0.0 (clamped from negative)."""
        from embedding_service import EmbeddingService
        
        vec_a = [1.0, 0.0, 0.0]
        vec_b = [-1.0, 0.0, 0.0]
        result = EmbeddingService.compute_similarity(vec_a, vec_b)
        assert result == 0.0
    
    def test_empty_vectors_return_zero(self):
        """Empty vectors should return 0.0."""
        from embedding_service import EmbeddingService
        
        assert EmbeddingService.compute_similarity([], []) == 0.0
        assert EmbeddingService.compute_similarity(None, [1.0]) == 0.0
        assert EmbeddingService.compute_similarity([1.0], None) == 0.0
    
    def test_dimension_mismatch_returns_zero(self):
        """Vectors with different dimensions should return 0.0."""
        from embedding_service import EmbeddingService
        
        result = EmbeddingService.compute_similarity([1.0, 2.0], [1.0, 2.0, 3.0])
        assert result == 0.0
    
    def test_zero_magnitude_returns_zero(self):
        """Zero-magnitude vector should return 0.0."""
        from embedding_service import EmbeddingService
        
        result = EmbeddingService.compute_similarity([0.0, 0.0], [1.0, 2.0])
        assert result == 0.0
    
    def test_known_similarity_value(self):
        """Test with known calculated similarity."""
        from embedding_service import EmbeddingService
        
        vec_a = [1.0, 2.0, 3.0]
        vec_b = [4.0, 5.0, 6.0]
        # Manual calculation:
        # dot = 1*4 + 2*5 + 3*6 = 32
        # |a| = sqrt(14), |b| = sqrt(77)
        # cos = 32 / sqrt(14*77) = 32 / sqrt(1078)
        expected = 32 / math.sqrt(14 * 77)
        result = EmbeddingService.compute_similarity(vec_a, vec_b)
        assert abs(result - expected) < 0.001


class TestDescriptionHash:
    """Tests for description hash computation."""
    
    def test_same_text_same_hash(self):
        """Identical text should produce identical hash."""
        from embedding_service import EmbeddingService
        
        h1 = EmbeddingService.compute_description_hash("Hello world")
        h2 = EmbeddingService.compute_description_hash("Hello world")
        assert h1 == h2
    
    def test_different_text_different_hash(self):
        """Different text should produce different hash."""
        from embedding_service import EmbeddingService
        
        h1 = EmbeddingService.compute_description_hash("Java developer needed")
        h2 = EmbeddingService.compute_description_hash("Python developer needed")
        assert h1 != h2
    
    def test_whitespace_normalization(self):
        """Extra whitespace should be normalized."""
        from embedding_service import EmbeddingService
        
        h1 = EmbeddingService.compute_description_hash("Hello  world")
        h2 = EmbeddingService.compute_description_hash("Hello world")
        assert h1 == h2
    
    def test_empty_text(self):
        """Empty text should produce a consistent hash."""
        from embedding_service import EmbeddingService
        
        h1 = EmbeddingService.compute_description_hash("")
        h2 = EmbeddingService.compute_description_hash("")
        assert h1 == h2
    
    def test_none_text(self):
        """None should be handled gracefully."""
        from embedding_service import EmbeddingService
        
        h = EmbeddingService.compute_description_hash(None)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex length
    
    def test_hash_is_64_chars(self):
        """Hash should be SHA-256 (64 hex chars)."""
        from embedding_service import EmbeddingService
        
        h = EmbeddingService.compute_description_hash("Test")
        assert len(h) == 64


class TestGenerateEmbedding:
    """Tests for embedding generation via OpenAI."""
    
    def test_generate_embedding_calls_openai(self):
        """Embedding generation should call OpenAI embeddings API."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1536)]
        mock_client.embeddings.create.return_value = mock_response
        service.openai_client = mock_client
        
        result = service.generate_embedding("Test resume text")
        
        assert result == [0.1] * 1536
        mock_client.embeddings.create.assert_called_once()
        call_kwargs = mock_client.embeddings.create.call_args
        assert call_kwargs[1]['model'] == 'text-embedding-3-small'
    
    def test_generate_embedding_returns_none_on_error(self):
        """Should return None if OpenAI call fails."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = Exception("API error")
        service.openai_client = mock_client
        
        result = service.generate_embedding("Test text")
        assert result is None
    
    def test_generate_embedding_returns_none_with_no_client(self):
        """Should return None if OpenAI client is not initialized."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        service.openai_client = None
        
        result = service.generate_embedding("Test text")
        assert result is None
    
    def test_generate_embedding_returns_none_for_empty_text(self):
        """Should return None for empty text."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        service.openai_client = MagicMock()
        
        assert service.generate_embedding("") is None
        assert service.generate_embedding("   ") is None
    
    def test_generate_embedding_truncates_long_text(self):
        """Long text should be intelligently truncated using head+tail strategy."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1536)]
        mock_client.embeddings.create.return_value = mock_response
        service.openai_client = mock_client
        
        # Create text that is definitely over 8000 tokens
        # Use real words to get predictable tiktoken counts
        long_text = "software engineer experience " * 5000  # ~10,000+ tokens
        service.generate_embedding(long_text)
        
        call_kwargs = mock_client.embeddings.create.call_args
        sent_text = call_kwargs[1]['input']
        # Should be truncated (shorter than original)
        assert len(sent_text) < len(long_text)
        assert "...[truncated]..." in sent_text
    
    def test_short_text_not_truncated(self):
        """Text under the token limit should pass through unchanged."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        short_text = "Python developer with 5 years experience"
        result, was_truncated, original_tokens = service._truncate_for_embedding(short_text)
        assert result == short_text
        assert was_truncated is False
        assert original_tokens > 0
    
    def test_truncation_preserves_head_and_tail(self):
        """Truncated text should keep the beginning and end of the original."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        # Create text with identifiable head and tail sections
        head_marker = "HEAD CONTACT INFO SKILLS "
        tail_marker = " TAIL EDUCATION CERTS"
        # Use repeating words to create predictable token count well over 8000
        middle = "experienced software developer working on projects " * 2000
        text = head_marker + middle + tail_marker
        
        result, was_truncated, original_tokens = service._truncate_for_embedding(text)
        
        assert was_truncated is True
        # Head should be preserved (starts with our marker)
        assert result.startswith(head_marker)
        # Tail should be preserved (ends with our marker)
        assert result.endswith(tail_marker)
        # Separator should be present
        assert "...[truncated]..." in result
        # Original token count should be reported
        assert original_tokens > 8000
    
    def test_truncation_logs_warning(self):
        """Truncation should log a warning with token counts."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        long_text = "software engineer experience " * 5000  # well over 8000 tokens
        
        with patch('embedding_service.logging') as mock_logging:
            result, was_truncated, original_tokens = service._truncate_for_embedding(long_text)
            assert was_truncated is True
            # Should have logged at least one warning about truncation
            assert mock_logging.warning.called
            warning_msg = mock_logging.warning.call_args[0][0]
            assert "truncated" in warning_msg.lower()
    
    def test_truncation_returns_metadata_tuple(self):
        """_truncate_for_embedding should return (text, was_truncated, original_tokens)."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        
        # Short text: no truncation
        result = service._truncate_for_embedding("hello world")
        assert isinstance(result, tuple)
        assert len(result) == 3
        text, was_truncated, token_count = result
        assert text == "hello world"
        assert was_truncated is False
        assert isinstance(token_count, int)
        assert token_count > 0
        
        # Long text: triggers truncation
        long_text = "data science machine learning " * 5000
        text2, was_truncated2, token_count2 = service._truncate_for_embedding(long_text)
        assert was_truncated2 is True
        assert token_count2 > 8000
        assert "...[truncated]..." in text2
    
    def test_count_tokens_basic(self):
        """count_tokens should return a positive integer for non-empty text."""
        from embedding_service import EmbeddingService
        
        count = EmbeddingService.count_tokens("Hello world, this is a test.")
        assert isinstance(count, int)
        assert count > 0
        # "Hello world, this is a test." is about 8 tokens
        assert 5 <= count <= 12
    
    def test_count_tokens_empty_text(self):
        """count_tokens should return 0 for empty or None text."""
        from embedding_service import EmbeddingService
        
        assert EmbeddingService.count_tokens("") == 0
        assert EmbeddingService.count_tokens(None) == 0
    
    def test_count_tokens_accuracy_vs_estimation(self):
        """tiktoken should give more accurate counts than char-based estimation."""
        from embedding_service import EmbeddingService, TIKTOKEN_AVAILABLE
        
        if not TIKTOKEN_AVAILABLE:
            pytest.skip("tiktoken not installed")
        
        # Technical text where char estimation is inaccurate
        technical_text = "API/ML: AWS SageMaker, CI/CD, ETL, NLP, LLM, GCP, k8s. " * 100
        
        actual_tokens = EmbeddingService.count_tokens(technical_text)
        char_estimate = len(technical_text) // 4  # Old estimation
        
        # The char estimate should differ from actual
        # (technical text with short tokens tends to have MORE tokens than estimated)
        assert actual_tokens != char_estimate
        # Token count should be reasonable (not wildly off)
        assert actual_tokens > 0
    
    def test_count_tokens_fallback_when_tiktoken_unavailable(self):
        """count_tokens should use conservative fallback if tiktoken is missing."""
        from embedding_service import EmbeddingService
        
        text = "This is a test sentence for token counting."
        
        # Mock tiktoken being unavailable
        with patch('embedding_service.TIKTOKEN_AVAILABLE', False):
            count = EmbeddingService.count_tokens(text)
            # Should use len(text) // 3 = 44 // 3 = 14
            assert count == len(text) // 3
    
    def test_truncation_within_token_budget(self):
        """After truncation, the result should fit within the token budget."""
        from embedding_service import EmbeddingService, TIKTOKEN_AVAILABLE
        
        if not TIKTOKEN_AVAILABLE:
            pytest.skip("tiktoken not installed")
        
        service = EmbeddingService()
        # Create text that is clearly over 8000 tokens  
        long_text = "data scientist with experience " * 5000
        
        result_text, was_truncated, original_tokens = service._truncate_for_embedding(long_text)
        assert was_truncated is True
        
        # Verify the truncated text is within budget
        truncated_tokens = EmbeddingService.count_tokens(result_text)
        # Allow small overhead for the separator text
        assert truncated_tokens <= 8100  # 8000 budget + separator overhead


class TestFilterRelevantJobs:
    """Tests for the core job filtering logic."""
    
    def test_filter_disabled_passes_all_jobs(self):
        """When killswitch is disabled, all jobs should pass through."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        service.is_filter_enabled = MagicMock(return_value=False)
        
        jobs = [
            {'id': 1, 'title': 'Job A', 'description': 'Desc A'},
            {'id': 2, 'title': 'Job B', 'description': 'Desc B'},
        ]
        
        result, filtered_count = service.filter_relevant_jobs(
            "Resume text", jobs, {'id': 100, 'name': 'Test'}, 1
        )
        
        assert len(result) == 2
        assert filtered_count == 0
    
    def test_filter_passes_relevant_jobs(self):
        """Jobs with high similarity should pass through."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        service.is_filter_enabled = MagicMock(return_value=True)
        service.get_similarity_threshold = MagicMock(return_value=0.25)
        
        # Mock embedding generation to return controlled vectors
        high_sim_vec = [1.0, 0.0, 0.0]  # Will be "similar" to resume
        low_sim_vec = [0.0, 1.0, 0.0]   # Will be "different"
        resume_vec = [0.9, 0.1, 0.0]    # Similar to high_sim_vec
        
        call_count = [0]
        def mock_generate(text):
            call_count[0] += 1
            if call_count[0] == 1:  # First call = resume
                return resume_vec
            return None  # Let get_job_embedding handle jobs
        
        service.generate_embedding = mock_generate
        service.get_job_embedding = MagicMock(side_effect=[high_sim_vec, low_sim_vec])
        service._save_filter_logs = MagicMock()
        
        jobs = [
            {'id': 1, 'title': 'Relevant Job', 'description': 'Python developer needed'},
            {'id': 2, 'title': 'Irrelevant Job', 'description': 'Nursing position'},
        ]
        
        result, filtered_count = service.filter_relevant_jobs(
            "Experienced Python developer", jobs, {'id': 100, 'name': 'Test'}, 1
        )
        
        assert len(result) == 1
        assert result[0]['id'] == 1
        assert filtered_count == 1
    
    def test_filter_passes_jobs_without_description(self):
        """Jobs with no description should pass through (safe fallback)."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        service.is_filter_enabled = MagicMock(return_value=True)
        service.get_similarity_threshold = MagicMock(return_value=0.25)
        service.generate_embedding = MagicMock(return_value=[0.1] * 10)
        
        jobs = [
            {'id': 1, 'title': 'No Description Job', 'description': ''},
            {'id': 2, 'title': 'Null Description Job'},
        ]
        
        result, filtered_count = service.filter_relevant_jobs(
            "Resume text", jobs, {'id': 100, 'name': 'Test'}, 1
        )
        
        assert len(result) == 2
        assert filtered_count == 0
    
    def test_filter_bypassed_on_resume_embedding_failure(self):
        """If resume embedding fails, all jobs should pass through."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        service.is_filter_enabled = MagicMock(return_value=True)
        service.generate_embedding = MagicMock(return_value=None)
        
        jobs = [{'id': 1, 'title': 'Job', 'description': 'Desc'}]
        
        result, filtered_count = service.filter_relevant_jobs(
            "Resume text", jobs, {'id': 100, 'name': 'Test'}, 1
        )
        
        assert len(result) == 1
        assert filtered_count == 0
    
    def test_filter_empty_jobs_list(self):
        """Empty jobs list should return empty."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        
        result, filtered_count = service.filter_relevant_jobs(
            "Resume text", [], {'id': 100, 'name': 'Test'}, 1
        )
        
        assert result == []
        assert filtered_count == 0
    
    def test_filter_empty_resume(self):
        """Empty resume should pass all jobs through."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        jobs = [{'id': 1, 'title': 'Job', 'description': 'Desc'}]
        
        result, filtered_count = service.filter_relevant_jobs(
            "", jobs, {'id': 100, 'name': 'Test'}, 1
        )
        
        assert len(result) == 1
        assert filtered_count == 0


class TestIsFilterEnabled:
    """Tests for the killswitch logic."""
    
    def test_env_var_true_enables_filter(self):
        """EMBEDDING_FILTER_ENABLED=true should enable filter."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        with patch.dict(os.environ, {'EMBEDDING_FILTER_ENABLED': 'true'}):
            assert service.is_filter_enabled() is True
    
    def test_env_var_false_disables_filter(self):
        """EMBEDDING_FILTER_ENABLED=false should disable filter."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        with patch.dict(os.environ, {'EMBEDDING_FILTER_ENABLED': 'false'}):
            assert service.is_filter_enabled() is False
    
    def test_env_var_takes_precedence(self):
        """Environment variable should take precedence over database config."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        with patch.dict(os.environ, {'EMBEDDING_FILTER_ENABLED': 'false'}):
            # Env var is checked first, so DB value doesn't matter
            assert service.is_filter_enabled() is False
    
    def test_default_enabled_when_no_config(self, app):
        """Filter should default to enabled when no config exists."""
        from embedding_service import EmbeddingService
        
        service = EmbeddingService()
        # Clear env var and test with app context for VettingConfig access
        env = os.environ.copy()
        if 'EMBEDDING_FILTER_ENABLED' in env:
            del env['EMBEDDING_FILTER_ENABLED']
        
        with app.app_context():
            with patch.dict(os.environ, env, clear=True):
                # No env var, no DB entry → should default to True
                assert service.is_filter_enabled() is True


class TestGetSimilarityThreshold:
    """Tests for threshold configuration reading."""
    
    def test_returns_default_threshold(self, app):
        """Should return 0.25 when no config exists."""
        from embedding_service import EmbeddingService, DEFAULT_SIMILARITY_THRESHOLD
        
        service = EmbeddingService()
        with app.app_context():
            # No DB entry → should return default
            result = service.get_similarity_threshold()
            assert result == DEFAULT_SIMILARITY_THRESHOLD
    
    def test_returns_configured_threshold(self, app):
        """Should return the configured threshold value."""
        from embedding_service import EmbeddingService
        from models import VettingConfig
        from app import db
        
        service = EmbeddingService()
        with app.app_context():
            # Use upsert pattern to avoid UNIQUE constraint violation
            # (seed_database may have already created this key)
            existing = VettingConfig.query.filter_by(
                setting_key='embedding_similarity_threshold'
            ).first()
            
            original_value = None
            if existing:
                original_value = existing.setting_value
                existing.setting_value = '0.30'
            else:
                config = VettingConfig(
                    setting_key='embedding_similarity_threshold',
                    setting_value='0.30'
                )
                db.session.add(config)
            db.session.commit()
            
            result = service.get_similarity_threshold()
            assert result == 0.30
            
            # Cleanup: restore original value or remove
            if original_value is not None:
                existing.setting_value = original_value
            else:
                config = VettingConfig.query.filter_by(
                    setting_key='embedding_similarity_threshold'
                ).first()
                if config:
                    db.session.delete(config)
            db.session.commit()


class TestJobEmbeddingCaching:
    """Tests for job embedding cache behavior."""
    
    def test_get_job_embedding_cache_hit(self, app):
        """Should return cached embedding when hash matches."""
        from embedding_service import EmbeddingService
        from models import JobEmbedding
        from app import db
        
        service = EmbeddingService()
        
        with app.app_context():
            # Pre-populate cache
            description = "Python developer needed"
            desc_hash = EmbeddingService.compute_description_hash(description)
            cached_vec = [0.5] * 1536
            
            entry = JobEmbedding(
                bullhorn_job_id=999,
                job_title="Test Job",
                description_hash=desc_hash,
                embedding_vector=json.dumps(cached_vec),
                embedding_model='text-embedding-3-small'
            )
            db.session.add(entry)
            db.session.commit()
            
            # Mock to verify generate_embedding is NOT called
            service.generate_embedding = MagicMock()
            
            result = service.get_job_embedding(999, description, "Test Job")
            
            assert result == cached_vec
            service.generate_embedding.assert_not_called()
            
            # Cleanup
            db.session.delete(entry)
            db.session.commit()
    
    def test_get_job_embedding_cache_miss(self, app):
        """Should generate and cache when no cache exists."""
        from embedding_service import EmbeddingService
        from models import JobEmbedding
        from app import db
        
        service = EmbeddingService()
        new_vec = [0.3] * 1536
        service.generate_embedding = MagicMock(return_value=new_vec)
        
        with app.app_context():
            result = service.get_job_embedding(998, "New job description", "New Job")
            
            assert result == new_vec
            service.generate_embedding.assert_called_once()
            
            # Verify it was cached
            cached = JobEmbedding.query.filter_by(bullhorn_job_id=998).first()
            assert cached is not None
            assert cached.job_title == "New Job"
            
            # Cleanup
            db.session.delete(cached)
            db.session.commit()
    
    def test_get_job_embedding_hash_change(self, app):
        """Should regenerate embedding when description hash changes."""
        from embedding_service import EmbeddingService
        from models import JobEmbedding
        from app import db
        
        service = EmbeddingService()
        
        with app.app_context():
            # Pre-populate with old hash
            old_vec = [0.1] * 1536
            entry = JobEmbedding(
                bullhorn_job_id=997,
                job_title="Job",
                description_hash="old_hash_that_wont_match",
                embedding_vector=json.dumps(old_vec),
                embedding_model='text-embedding-3-small'
            )
            db.session.add(entry)
            db.session.commit()
            
            # New description with different hash
            new_vec = [0.9] * 1536
            service.generate_embedding = MagicMock(return_value=new_vec)
            
            result = service.get_job_embedding(997, "Updated job description", "Job")
            
            assert result == new_vec
            service.generate_embedding.assert_called_once()
            
            # Verify cache was updated
            cached = JobEmbedding.query.filter_by(bullhorn_job_id=997).first()
            assert json.loads(cached.embedding_vector) == new_vec
            
            # Cleanup
            db.session.delete(cached)
            db.session.commit()


class TestFilterLogCreation:
    """Tests for EmbeddingFilterLog audit trail."""
    
    def test_save_filter_logs(self, app):
        """Filtered pairs should be saved to EmbeddingFilterLog."""
        from embedding_service import EmbeddingService
        from models import EmbeddingFilterLog
        from app import db
        
        service = EmbeddingService()
        
        with app.app_context():
            entries = [
                {
                    'bullhorn_candidate_id': 100,
                    'candidate_name': 'Test Candidate',
                    'bullhorn_job_id': 200,
                    'job_title': 'Irrelevant Job',
                    'similarity_score': 0.12,
                    'threshold_used': 0.25,
                    'resume_snippet': 'Python developer with 5 years...',
                    'vetting_log_id': None
                }
            ]
            
            service._save_filter_logs(entries)
            
            # Verify log was created
            log = EmbeddingFilterLog.query.filter_by(bullhorn_candidate_id=100).first()
            assert log is not None
            assert log.similarity_score == 0.12
            assert log.threshold_used == 0.25
            assert log.job_title == 'Irrelevant Job'
            
            # Cleanup
            db.session.delete(log)
            db.session.commit()


class TestEscalationLogging:
    """Tests for EscalationLog tracking."""
    
    def test_save_escalation_log_material_change(self, app):
        """Escalation with >=5 point delta should be flagged as material."""
        from embedding_service import EmbeddingService
        from models import EscalationLog
        from app import db
        
        service = EmbeddingService()
        
        with app.app_context():
            service.save_escalation_log(
                vetting_log_id=None,
                candidate_id=300,
                candidate_name='Borderline Candidate',
                job_id=400,
                job_title='Test Job',
                mini_score=72.0,
                gpt4o_score=78.0,
                threshold=80.0
            )
            
            log = EscalationLog.query.filter_by(bullhorn_candidate_id=300).first()
            assert log is not None
            assert log.mini_score == 72.0
            assert log.gpt4o_score == 78.0
            assert log.score_delta == 6.0
            assert log.material_change is True  # |6| >= 5
            assert log.crossed_threshold is False  # Both below 80
            
            # Cleanup
            db.session.delete(log)
            db.session.commit()
    
    def test_save_escalation_log_threshold_crossing(self, app):
        """Escalation that changes recommendation should flag threshold crossing."""
        from embedding_service import EmbeddingService
        from models import EscalationLog
        from app import db
        
        service = EmbeddingService()
        
        with app.app_context():
            service.save_escalation_log(
                vetting_log_id=None,
                candidate_id=301,
                candidate_name='Threshold Crosser',
                job_id=401,
                job_title='Important Job',
                mini_score=78.0,
                gpt4o_score=83.0,
                threshold=80.0
            )
            
            log = EscalationLog.query.filter_by(bullhorn_candidate_id=301).first()
            assert log is not None
            assert log.crossed_threshold is True  # 78 < 80 but 83 >= 80
            assert log.material_change is True  # |5| >= 5
            
            # Cleanup
            db.session.delete(log)
            db.session.commit()
    
    def test_save_escalation_log_minor_change(self, app):
        """Escalation with <5 point delta should NOT be flagged as material."""
        from embedding_service import EmbeddingService
        from models import EscalationLog
        from app import db
        
        service = EmbeddingService()
        
        with app.app_context():
            service.save_escalation_log(
                vetting_log_id=None,
                candidate_id=302,
                candidate_name='Minor Change Candidate',
                job_id=402,
                job_title='Some Job',
                mini_score=70.0,
                gpt4o_score=73.0,
                threshold=80.0
            )
            
            log = EscalationLog.query.filter_by(bullhorn_candidate_id=302).first()
            assert log is not None
            assert log.material_change is False  # |3| < 5
            assert log.crossed_threshold is False
            
            # Cleanup
            db.session.delete(log)
            db.session.commit()


class TestEscalationRange:
    """Tests for escalation range configuration in CandidateVettingService."""
    
    def test_should_escalate_in_range(self, app):
        """Scores within escalation range should trigger escalation."""
        with app.app_context():
            from candidate_vetting_service import CandidateVettingService
            
            service = CandidateVettingService()
            service.get_config_value = MagicMock(side_effect=lambda key, default=None: {
                'escalation_low': '60',
                'escalation_high': '85',
                'layer2_model': 'gpt-4o-mini'
            }.get(key, default))
            
            assert service.should_escalate_to_gpt4o(60) is True
            assert service.should_escalate_to_gpt4o(72) is True
            assert service.should_escalate_to_gpt4o(85) is True
    
    def test_should_not_escalate_outside_range(self, app):
        """Scores outside escalation range should NOT trigger escalation."""
        with app.app_context():
            from candidate_vetting_service import CandidateVettingService
            
            service = CandidateVettingService()
            service.get_config_value = MagicMock(side_effect=lambda key, default=None: {
                'escalation_low': '60',
                'escalation_high': '85',
                'layer2_model': 'gpt-4o-mini'
            }.get(key, default))
            
            assert service.should_escalate_to_gpt4o(59) is False
            assert service.should_escalate_to_gpt4o(30) is False
            assert service.should_escalate_to_gpt4o(86) is False
            assert service.should_escalate_to_gpt4o(95) is False
    
    def test_escalation_range_configurable(self, app):
        """Escalation range should be read from VettingConfig."""
        with app.app_context():
            from candidate_vetting_service import CandidateVettingService
            
            service = CandidateVettingService()
            # Simulate tightened range
            service.get_config_value = MagicMock(side_effect=lambda key, default=None: {
                'escalation_low': '70',
                'escalation_high': '82',
                'layer2_model': 'gpt-4o-mini'
            }.get(key, default))
            
            assert service.should_escalate_to_gpt4o(65) is False  # Below tightened range
            assert service.should_escalate_to_gpt4o(75) is True   # In range
            assert service.should_escalate_to_gpt4o(83) is False  # Above tightened range


class TestLayer2Model:
    """Tests for Layer 2 model configuration."""
    
    def test_default_model_is_gpt4o(self, app):
        """Default Layer 2 model should be gpt-4o."""
        with app.app_context():
            from candidate_vetting_service import CandidateVettingService
            
            service = CandidateVettingService()
            # With no config, should default to gpt-4o
            assert service._get_layer2_model() == 'gpt-4o'
    
    def test_model_configurable_via_vetting_config(self, app):
        """Layer 2 model should be changeable via VettingConfig."""
        with app.app_context():
            from candidate_vetting_service import CandidateVettingService
            
            service = CandidateVettingService()
            service.get_config_value = MagicMock(side_effect=lambda key, default=None: {
                'layer2_model': 'gpt-4o',
            }.get(key, default))
            
            assert service._get_layer2_model() == 'gpt-4o'


class TestModelOverride:
    """Tests for the model_override parameter in analyze_candidate_job_match."""
    
    def test_model_override_used_when_provided(self, app):
        """model_override should take precedence over self.model."""
        with app.app_context():
            from candidate_vetting_service import CandidateVettingService
            
            service = CandidateVettingService()
            service.model = 'gpt-4o'
            
            # Mock OpenAI call
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = json.dumps({
                'match_score': 75,
                'match_summary': 'Good match',
                'skills_match': 'Python',
                'experience_match': '5 years',
                'gaps_identified': 'None',
                'key_requirements': 'Python, SQL'
            })
            
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            service.openai_client = mock_client
            
            job = {'id': 1, 'title': 'Test Job', 'description': 'Need a Python dev', 'publicDescription': 'Python dev needed'}
            
            service.analyze_candidate_job_match(
                "Python developer resume",
                job,
                model_override='gpt-4o'
            )
            
            call_kwargs = mock_client.chat.completions.create.call_args
            assert call_kwargs[1]['model'] == 'gpt-4o'
    
    def test_self_model_used_when_no_override(self, app):
        """self.model should be used when no override is provided."""
        with app.app_context():
            from candidate_vetting_service import CandidateVettingService
            
            service = CandidateVettingService()
            service.model = 'gpt-4o'
            
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = json.dumps({
                'match_score': 75,
                'match_summary': 'Good match',
                'skills_match': 'Python',
                'experience_match': '5 years',
                'gaps_identified': 'None',
                'key_requirements': 'Python, SQL'
            })
            
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            service.openai_client = mock_client
            
            job = {'id': 1, 'title': 'Test Job', 'description': 'Need a Python dev', 'publicDescription': 'Python dev needed'}
            
            service.analyze_candidate_job_match(
                "Python developer resume",
                job
            )
            
            call_kwargs = mock_client.chat.completions.create.call_args
            assert call_kwargs[1]['model'] == 'gpt-4o'
