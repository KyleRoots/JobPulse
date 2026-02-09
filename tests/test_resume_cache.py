"""
Tests for Resume Parsing Cache functionality
Tests cache hit/miss behavior to ensure GPT-4o API savings
"""
import pytest
import hashlib
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


class TestParsedResumeCacheModel:
    """Tests for the ParsedResumeCache database model"""
    
    def test_cache_model_exists(self, app):
        """Verify ParsedResumeCache model is properly defined"""
        from models import ParsedResumeCache
        assert ParsedResumeCache is not None
        assert hasattr(ParsedResumeCache, 'content_hash')
        assert hasattr(ParsedResumeCache, 'parsed_data_json')
        assert hasattr(ParsedResumeCache, 'raw_text')
        assert hasattr(ParsedResumeCache, 'formatted_html')
        assert hasattr(ParsedResumeCache, 'access_count')
        assert hasattr(ParsedResumeCache, 'CACHE_TTL_DAYS')
    
    def test_cache_store_creates_entry(self, app):
        """Verify store() creates a new cache entry"""
        from models import ParsedResumeCache
        from app import db
        
        test_hash = hashlib.sha256(b"test content").hexdigest()
        test_data = {'first_name': 'John', 'last_name': 'Doe', 'email': 'john@test.com', 'phone': '555-1234'}
        
        # Store in cache
        cached = ParsedResumeCache.store(
            content_hash=test_hash,
            parsed_data=test_data,
            raw_text="John Doe\njohn@test.com",
            formatted_html="<p>John Doe</p>"
        )
        
        assert cached is not None
        assert cached.content_hash == test_hash
        assert cached.access_count == 1
        
        # Cleanup
        db.session.delete(cached)
        db.session.commit()
    
    def test_cache_get_returns_cached_data(self, app):
        """Verify get_cached() retrieves existing entry"""
        from models import ParsedResumeCache
        from app import db
        
        test_hash = hashlib.sha256(b"test getting cached").hexdigest()
        test_data = {'first_name': 'Jane', 'last_name': 'Smith', 'email': 'jane@test.com', 'phone': None}
        
        # Store first
        ParsedResumeCache.store(
            content_hash=test_hash,
            parsed_data=test_data,
            raw_text="Jane Smith",
            formatted_html="<p>Jane Smith</p>"
        )
        
        # Retrieve
        cached = ParsedResumeCache.get_cached(test_hash)
        assert cached is not None
        assert json.loads(cached.parsed_data_json) == test_data
        assert cached.access_count == 2  # Incremented on get
        
        # Cleanup
        db.session.delete(cached)
        db.session.commit()
    
    def test_cache_miss_returns_none(self, app):
        """Verify get_cached() returns None for non-existent hash"""
        from models import ParsedResumeCache
        
        fake_hash = hashlib.sha256(b"this does not exist").hexdigest()
        result = ParsedResumeCache.get_cached(fake_hash)
        assert result is None


class TestResumeParserCacheIntegration:
    """Tests for cache integration in ResumeParser"""
    
    def test_parser_has_cache_methods(self):
        """Verify ResumeParser has cache-related methods"""
        from resume_parser import ResumeParser
        
        parser = ResumeParser()
        assert hasattr(parser, '_get_content_hash')
        assert hasattr(parser, '_check_cache')
        assert hasattr(parser, '_store_cache')
        assert hasattr(parser, '_cache_enabled')
    
    def test_content_hash_is_consistent(self):
        """Verify same content produces same hash"""
        from resume_parser import ResumeParser
        
        parser = ResumeParser()
        content = b"Resume content for testing"
        
        hash1 = parser._get_content_hash(content)
        hash2 = parser._get_content_hash(content)
        
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 produces 64 hex chars
    
    def test_different_content_different_hash(self):
        """Verify different content produces different hashes"""
        from resume_parser import ResumeParser
        
        parser = ResumeParser()
        
        hash1 = parser._get_content_hash(b"Resume version 1")
        hash2 = parser._get_content_hash(b"Resume version 2")
        
        assert hash1 != hash2
    
    def test_parse_resume_has_skip_cache_param(self):
        """Verify parse_resume accepts skip_cache parameter"""
        from resume_parser import ResumeParser
        import inspect
        
        sig = inspect.signature(ResumeParser.parse_resume)
        params = list(sig.parameters.keys())
        
        assert 'skip_cache' in params
        assert 'candidate_id' in params


class TestCacheEnvironmentToggle:
    """Tests for cache enable/disable via environment variable"""
    
    def test_cache_enabled_by_default(self):
        """Verify cache is enabled by default"""
        from resume_parser import ResumeParser
        
        parser = ResumeParser()
        assert parser._cache_enabled is True
    
    @patch.dict('os.environ', {'RESUME_CACHE_ENABLED': 'false'})
    def test_cache_disabled_via_env(self):
        """Verify cache can be disabled via environment variable"""
        from resume_parser import ResumeParser
        
        parser = ResumeParser()
        assert parser._cache_enabled is False
