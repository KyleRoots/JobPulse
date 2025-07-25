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
                'job_function': '',
                'job_industry': '',
                'seniority_level': ''
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
            1. You MUST select values ONLY from the provided lists
            2. Select the SINGLE BEST match for each category
            3. For seniority level, consider keywords like "Senior", "Junior", "Lead", "Manager", "Director", etc.
            4. If unsure, choose "Not Applicable" for seniority level
            
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
            
            self.logger.info(f"Classified job '{job_title}': Function={validated_result['job_function']}, "
                           f"Industry={validated_result['job_industry']}, "
                           f"Seniority={validated_result['seniority_level']}")
            
            return validated_result
            
        except Exception as e:
            self.logger.error(f"Error classifying job: {e}")
            return {
                'job_function': '',
                'job_industry': '',
                'seniority_level': ''
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
        
        # Validate job industry
        job_industry = classification.get('job_industry', '')
        if job_industry in self.categories['job_industries']:
            validated['job_industry'] = job_industry
        else:
            self.logger.warning(f"Invalid job industry '{job_industry}' - not in predefined list")
            validated['job_industry'] = ''
        
        # Validate seniority level
        seniority_level = classification.get('seniority_level', '')
        if seniority_level in self.categories['seniority_levels']:
            validated['seniority_level'] = seniority_level
        else:
            self.logger.warning(f"Invalid seniority level '{seniority_level}' - not in predefined list")
            validated['seniority_level'] = ''
        
        return validated
    
    def get_available_categories(self) -> Dict[str, List[str]]:
        """Get all available categories for reference"""
        return self.categories