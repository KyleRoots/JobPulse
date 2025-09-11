"""
Job Classification Service using Internal Keyword-Based Classification
Analyzes job titles and descriptions to classify them into predefined categories
"""

import os
import json
import re
import logging
from typing import Dict, List, Optional, Tuple
from openai import OpenAI

class InternalJobClassifier:
    """Fast, reliable internal classifier using keyword-based matching"""
    
    def __init__(self, categories: Dict):
        self.logger = logging.getLogger(__name__)
        self.categories = categories
        
        # Job Function Keywords - mapping keywords to function categories
        self.function_keywords = {
            'Information Technology': [
                'developer', 'programmer', 'software', 'tech', 'it', 'system', 'network', 'database',
                'devops', 'cloud', 'cybersecurity', 'security', 'data', 'web', 'mobile', 'api',
                'frontend', 'backend', 'fullstack', 'full stack', 'ui', 'ux', 'qa', 'testing'
            ],
            'Engineering': [
                'engineer', 'engineering', 'technical', 'fpga', 'hardware', 'design engineer',
                'asic', 'rtl', 'analog', 'digital', 'embedded', 'firmware', 'machine learning', 'ml'
            ],
            'Management': [
                'manager', 'director', 'head of', 'chief', 'vp', 'vice president', 'ceo', 'cto',
                'lead', 'supervisor', 'coordinator', 'team lead'
            ],
            'Consulting': [
                'consultant', 'consulting', 'advisor', 'specialist', 'sap', 'implementation'
            ],
            'Sales': [
                'sales', 'account', 'business development', 'bd', 'revenue', 'client relations'
            ],
            'Marketing': [
                'marketing', 'digital marketing', 'brand', 'campaign', 'social media', 'seo'
            ],
            'Project Management': [
                'project manager', 'pm', 'scrum master', 'agile', 'program manager'
            ],
            'Analyst': [
                'analyst', 'analysis', 'research', 'data analyst', 'business analyst'
            ],
            'Customer Service': [
                'customer service', 'support', 'customer support', 'help desk', 'technical support'
            ],
            'Quality Assurance': [
                'qa', 'quality', 'testing', 'test engineer', 'automation'
            ],
            'Design': [
                'designer', 'design', 'creative', 'graphic', 'ui designer', 'ux designer'
            ],
            'Finance': [
                'finance', 'financial', 'accounting', 'accountant', 'controller', 'cfo'
            ],
            'Human Resources': [
                'hr', 'human resources', 'recruiter', 'talent', 'people'
            ],
            'Legal': [
                'legal', 'lawyer', 'attorney', 'compliance', 'contract'
            ]
        }
        
        # Industry Keywords - mapping keywords to industry categories
        self.industry_keywords = {
            'Computer Software': [
                'software', 'saas', 'tech', 'technology', 'app', 'application', 'platform',
                'digital', 'cloud', 'ai', 'artificial intelligence'
            ],
            'Information Technology and Services': [
                'it services', 'information technology', 'tech consulting', 'system integration',
                'managed services', 'infrastructure'
            ],
            'Banking': [
                'bank', 'banking', 'financial institution', 'credit union', 'mortgage'
            ],
            'Financial Services': [
                'financial', 'finance', 'investment', 'capital', 'wealth management', 'fintech'
            ],
            'Insurance': [
                'insurance', 'insurer', 'underwriting', 'actuarial', 'claims'
            ],
            'Computer Hardware': [
                'hardware', 'semiconductor', 'chip', 'fpga', 'asic', 'circuit', 'silicon'
            ],
            'Computer Networking': [
                'networking', 'network', 'cisco', 'router', 'switch', 'infrastructure'
            ],
            'Computer and Network Security': [
                'security', 'cybersecurity', 'cyber', 'infosec', 'threat', 'vulnerability'
            ],
            'Semiconductors': [
                'semiconductor', 'chip', 'wafer', 'fab', 'foundry', 'silicon'
            ],
            'Consumer Electronics': [
                'consumer electronics', 'electronics', 'devices', 'gadgets', 'hardware'
            ],
            'Aviation and Aerospace': [
                'aviation', 'aerospace', 'aircraft', 'flight', 'airline', 'defense'
            ],
            'Healthcare': [
                'healthcare', 'health', 'medical', 'hospital', 'clinic', 'pharmaceutical'
            ],
            'Manufacturing': [
                'manufacturing', 'production', 'factory', 'industrial', 'assembly'
            ],
            'Telecommunications': [
                'telecom', 'telecommunications', 'wireless', 'mobile', 'network operator'
            ]
        }
        
        # Seniority Keywords - mapping keywords to seniority levels
        self.seniority_keywords = {
            'Executive': [
                'ceo', 'cto', 'cfo', 'coo', 'chief', 'president', 'founder', 'executive'
            ],
            'Director': [
                'director', 'vp', 'vice president', 'head of', 'principal'
            ],
            'Mid-Senior level': [
                'senior', 'sr', 'lead', 'staff', 'principal engineer', 'architect'
            ],
            'Entry level': [
                'junior', 'jr', 'entry', 'graduate', 'trainee', 'associate', 'new grad'
            ],
            'Internship': [
                'intern', 'internship', 'student', 'co-op', 'coop'
            ]
        }
        
        self.logger.info("🚀 Internal job classifier initialized with keyword-based matching")
    
    def classify_job(self, title: str, description: str) -> Dict[str, str]:
        """
        Classify a job using keyword-based matching - instant and reliable!
        
        Returns same format as OpenAI classifier for drop-in replacement
        """
        # Clean and normalize inputs
        title_lower = title.lower().strip()
        desc_lower = description.lower().strip() if description else ""
        combined_text = f"{title_lower} {desc_lower}"
        
        # Classify job function
        job_function = self._classify_function(title_lower, combined_text)
        
        # Classify industry
        industry = self._classify_industry(title_lower, combined_text)
        
        # Classify seniority
        seniority = self._classify_seniority(title_lower)
        
        result = {
            'success': True,
            'job_function': job_function,
            'industries': industry,  # Note: using 'industries' key to match OpenAI format
            'seniority_level': seniority
        }
        
        self.logger.debug(f"Classified '{title}': Function={job_function}, Industry={industry}, Seniority={seniority}")
        return result
    
    def _classify_function(self, title: str, combined_text: str) -> str:
        """Classify job function based on keywords"""
        # Score each function category
        scores = {}
        for function, keywords in self.function_keywords.items():
            score = 0
            for keyword in keywords:
                # Higher weight for title matches
                if keyword in title:
                    score += 3
                elif keyword in combined_text:
                    score += 1
            scores[function] = score
        
        # Return highest scoring function
        if scores and max(scores.values()) > 0:
            best_function = max(scores, key=scores.get)
            return best_function
        
        # Default fallback
        return 'Information Technology'
    
    def _classify_industry(self, title: str, combined_text: str) -> str:
        """Classify industry based on keywords"""
        # Score each industry category
        scores = {}
        for industry, keywords in self.industry_keywords.items():
            score = 0
            for keyword in keywords:
                # Higher weight for title matches
                if keyword in title:
                    score += 3
                elif keyword in combined_text:
                    score += 1
            scores[industry] = score
        
        # Return highest scoring industry
        if scores and max(scores.values()) > 0:
            best_industry = max(scores, key=scores.get)
            return best_industry
        
        # Default fallback
        return 'Computer Software'
    
    def _classify_seniority(self, title: str) -> str:
        """Classify seniority level based on title keywords"""
        # Check for seniority indicators in title
        for seniority, keywords in self.seniority_keywords.items():
            for keyword in keywords:
                if keyword in title:
                    return seniority
        
        # Default fallback
        return 'Mid-Senior level'

class JobClassificationService:
    """Service for classifying jobs using fast, reliable internal classification"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Load predefined categories first
        self._load_categories()
        
        # Initialize internal classifier (fast and reliable!)
        self.internal_classifier = InternalJobClassifier(self.categories)
        
        # Optional OpenAI fallback (now unused but kept for compatibility)
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            self.openai_client = OpenAI(api_key=api_key, timeout=8.0)
            self.logger.info("⚡ Internal keyword-based classifier active (OpenAI available as fallback)")
        else:
            self.openai_client = None
            self.logger.info("⚡ Internal keyword-based classifier active (no OpenAI dependency)")
    
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
    
    def classify_jobs_batch(self, jobs: List[Dict[str, str]], batch_size: int = 10, max_retries: int = 2, max_processing_time: int = None) -> List[Dict[str, str]]:
        """
        Classify multiple jobs using fast internal classification - NO TIMEOUTS!
        
        Args:
            jobs: List of dicts with 'title' and 'description' keys
            batch_size: Ignored (internal classification is instant)
            max_retries: Ignored (internal classification is reliable)
            max_processing_time: Ignored (internal classification completes in milliseconds)
            
        Returns:
            List of classification results in same order as input
        """
        import time
        start_time = time.time()
        
        self.logger.info(f"⚡ Processing {len(jobs)} jobs with internal keyword-based classification...")
        
        results = []
        for job in jobs:
            try:
                # Use internal classifier - instant results!
                result = self.internal_classifier.classify_job(job['title'], job['description'])
                results.append(result)
            except Exception as e:
                self.logger.error(f"Internal classification error for job '{job['title']}': {e}")
                # Even failures are handled quickly
                results.append({
                    'success': False, 
                    'job_function': '', 
                    'industries': '', 
                    'seniority_level': '', 
                    'error': str(e)
                })
        
        elapsed_total = time.time() - start_time
        self.logger.info(f"✅ Internal classification completed: {len(results)} jobs processed in {elapsed_total:.3f}s")
        return results
    
    def classify_job(self, job_title: str, job_description: str) -> Dict[str, str]:
        """
        Classify a job using fast internal keyword-based classification
        
        Args:
            job_title: The job title
            job_description: The job description (can include HTML)
            
        Returns:
            Dict with 'job_function', 'industries', and 'seniority_level'
        """
        # Use internal classifier - instant and reliable!
        return self.internal_classifier.classify_job(job_title, job_description)
    
        """
        Intelligently map AI-suggested industries to our predefined categories
        
