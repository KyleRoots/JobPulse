"""
Job Classification Service using OpenAI
Analyzes job titles and descriptions to classify them into predefined categories
"""

import os
import json
import logging
from typing import Dict, List, Optional, Tuple
from openai import OpenAI

class JobClassificationService:
    """Service for classifying jobs into predefined categories using AI"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Initialize OpenAI client
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            self.openai_client = OpenAI(api_key=api_key)
        else:
            self.openai_client = None
            self.logger.warning("OPENAI_API_KEY not found - AI classification will not be available")
        
        # Load predefined categories
        self._load_categories()
    
    def _load_categories(self):
        """Load predefined job categories from JSON file"""
        try:
            with open('job_categories_mapping.json', 'r') as f:
                self.categories = json.load(f)
            self.logger.info(f"Loaded job categories: {len(self.categories['job_functions'])} functions, "
                           f"{len(self.categories['job_industries'])} industries, "
                           f"{len(self.categories['seniority_levels'])} seniority levels")
        except Exception as e:
            self.logger.error(f"Failed to load job categories: {e}")
            self.categories = {
                'job_functions': [],
                'job_industries': [],
                'seniority_levels': []
            }
    
    def classify_jobs_batch(self, jobs: List[Dict[str, str]], batch_size: int = 10, max_retries: int = 2) -> List[Dict[str, str]]:
        """
        Classify multiple jobs in batches to prevent timeouts
        
        Args:
            jobs: List of dicts with 'title' and 'description' keys
            batch_size: Number of jobs to process per batch
            max_retries: Number of retry attempts for failed classifications
            
        Returns:
            List of classification results in same order as input
        """
        if not self.openai_client:
            self.logger.warning("OpenAI client not initialized - returning empty classifications for batch")
            return [{'success': False, 'job_function': '', 'industries': '', 'seniority_level': '', 'error': 'OpenAI client not initialized'} for _ in jobs]
        
        results = []
        total_jobs = len(jobs)
        
        for i in range(0, total_jobs, batch_size):
            batch = jobs[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (total_jobs + batch_size - 1) // batch_size
            
            self.logger.info(f"Processing AI classification batch {batch_num}/{total_batches} ({len(batch)} jobs)")
            
            batch_results = []
            for job in batch:
                for attempt in range(max_retries + 1):
                    try:
                        result = self.classify_job(job['title'], job['description'])
                        batch_results.append(result)
                        break
                    except Exception as e:
                        if attempt < max_retries:
                            self.logger.warning(f"Retry {attempt + 1}/{max_retries} for job '{job['title']}': {e}")
                            import time
                            time.sleep(1)  # Brief delay before retry
                        else:
                            self.logger.error(f"Failed to classify job '{job['title']}' after {max_retries} retries: {e}")
                            batch_results.append({'success': False, 'job_function': '', 'industries': '', 'seniority_level': '', 'error': str(e)})
            
            results.extend(batch_results)
            
            # Brief pause between batches to prevent rate limiting
            if i + batch_size < total_jobs:
                import time
                time.sleep(0.5)
        
        self.logger.info(f"Completed batch AI classification: {len(results)} jobs processed")
        return results
    
    def classify_job(self, job_title: str, job_description: str) -> Dict[str, str]:
        """
        Classify a job based on its title and description
        
        Args:
            job_title: The job title
            job_description: The job description (can include HTML)
            
        Returns:
            Dict with 'job_function', 'job_industry', and 'seniority_level'
        """
        if not self.openai_client:
            self.logger.warning("OpenAI client not initialized - returning empty classifications")
            return {
                'success': False,
                'job_function': '',
                'industries': '',
                'seniority_level': '',
                'error': 'OpenAI client not initialized'
            }
        
        try:
            # Strip HTML tags from description for cleaner analysis
            import re
            clean_description = re.sub('<.*?>', '', job_description)
            
            # Create the prompt for OpenAI
            prompt = f"""
            You are a job classification expert. Analyze the following job posting and classify it into the predefined categories.
            
            Job Title: {job_title}
            Job Description: {clean_description[:1500]}  # Limit description length
            
            Based on this information, select the MOST APPROPRIATE single value from each of the following lists:
            
            Job Functions (select ONE):
            {', '.join(self.categories['job_functions'][:30])}
            ... and {len(self.categories['job_functions']) - 30} more
            
            Job Industries (select ONE):
            {', '.join(self.categories['job_industries'][:30])}
            ... and {len(self.categories['job_industries']) - 30} more
            
            Seniority Levels (select ONE):
            {', '.join(self.categories['seniority_levels'])}
            
            Important rules:
            1. You MUST select values ONLY from the provided lists - DO NOT create custom values
            2. Select the SINGLE BEST match for each category
            3. For seniority level: "Senior" roles should map to "Mid-Senior level", NOT "Senior level"
            4. Use "Executive" for C-level positions, "Director" for director roles
            5. If uncertain about seniority, default to "Mid-Senior level" for experienced roles
            
            Return your response in this exact JSON format:
            {{
                "job_function": "selected function from list",
                "job_industry": "selected industry from list",
                "seniority_level": "selected level from list"
            }}
            """
            
            response = self.openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a precise job classification expert. Always return valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.3  # Lower temperature for more consistent classifications
            )
            
            result = json.loads(response.choices[0].message.content)
            
            # Validate that returned values are in our predefined lists
            validated_result = self._validate_classification(result)
            
            # Add success flag and format keys to match monitoring expectations
            final_result = {
                'success': True,
                'job_function': validated_result['job_function'],
                'industries': validated_result['job_industry'], 
                'seniority_level': validated_result['seniority_level']
            }
            
            self.logger.info(f"Classified job '{job_title}': Function={final_result['job_function']}, "
                           f"Industry={final_result['industries']}, "
                           f"Seniority={final_result['seniority_level']}")
            
            return final_result
            
        except Exception as e:
            self.logger.error(f"Error classifying job: {e}")
            return {
                'success': False,
                'job_function': '',
                'industries': '',
                'seniority_level': '',
                'error': str(e)
            }
    
    def _validate_classification(self, classification: Dict) -> Dict[str, str]:
        """
        Validate that classification values exist in predefined lists
        
        Args:
            classification: Dict with job_function, job_industry, and seniority_level
            
        Returns:
            Validated classification with empty strings for invalid values
        """
        validated = {}
        
        # Validate job function
        job_function = classification.get('job_function', '')
        if job_function in self.categories['job_functions']:
            validated['job_function'] = job_function
        else:
            self.logger.warning(f"Invalid job function '{job_function}' - not in predefined list")
            validated['job_function'] = ''
        
        # Validate job industry with intelligent mapping
        job_industry = classification.get('job_industry', '')
        if job_industry in self.categories['job_industries']:
            validated['job_industry'] = job_industry
        else:
            # Try intelligent mapping for common AI suggestions
            mapped_industry = self._map_industry_intelligently(job_industry)
            if mapped_industry:
                validated['job_industry'] = mapped_industry
                self.logger.info(f"Mapped industry '{job_industry}' → '{mapped_industry}'")
            else:
                self.logger.warning(f"Could not map industry '{job_industry}' to predefined categories")
                validated['job_industry'] = ''
        
        # Validate seniority level
        seniority_level = classification.get('seniority_level', '')
        if seniority_level in self.categories['seniority_levels']:
            validated['seniority_level'] = seniority_level
        else:
            self.logger.warning(f"Invalid seniority level '{seniority_level}' - not in predefined list")
            validated['seniority_level'] = ''
        
        return validated
    
    def _map_industry_intelligently(self, suggested_industry: str) -> str:
        """
        Intelligently map AI-suggested industries to our predefined categories
        
        Args:
            suggested_industry: Industry suggested by AI
            
        Returns:
            str: Mapped industry from predefined list or empty string
        """
        if not suggested_industry:
            return ''
        
        industry_lower = suggested_industry.lower()
        valid_industries = self.categories['job_industries']
        
        # Direct mappings for common AI suggestions
        mappings = {
            'health care': 'Hospitals and Health Care',
            'healthcare': 'Hospitals and Health Care', 
            'medical': 'Medical Devices',
            'manufacturing': 'Electrical/Electronic Manufacturing',
            'information technology': 'Information Technology and Services',
            'it services': 'Information Technology and Services',
            'technology': 'Computer Software',
            'software': 'Computer Software',
            'legal': 'Legal Services',
            'professional services': 'Professional Training and Coaching',
            'oil & energy': 'Oil and Energy',
            'oil and gas': 'Oil and Energy',
            'energy': 'Oil and Energy',
            'project management': 'Management Consulting',
            'consulting': 'Management Consulting',
            'management consulting': 'Management Consulting',
            'business services': 'Management Consulting',
            'other': 'Management Consulting',  # Default fallback
            'general': 'Management Consulting'  # Default fallback
        }
        
        # Check direct mappings first
        if industry_lower in mappings:
            return mappings[industry_lower]
        
        # Pattern-based matching for partial matches
        for pattern, target in mappings.items():
            if pattern in industry_lower:
                return target
        
        # Fuzzy matching - look for keywords in predefined industries
        keywords_to_check = [
            ('health', 'medical', 'hospital'),
            ('manufacturing', 'industrial'),
            ('technology', 'software', 'computer'),
            ('legal', 'law'),
            ('energy', 'oil'),
            ('services', 'consulting')
        ]
        
        for keyword_group in keywords_to_check:
            if any(keyword in industry_lower for keyword in keyword_group):
                for valid_industry in valid_industries:
                    if any(keyword in valid_industry.lower() for keyword in keyword_group):
                        return valid_industry
        
        return ''
    
    def get_available_categories(self) -> Dict[str, List[str]]:
        """Get all available categories for reference"""
        return self.categories