"""
Job Classification Service using AI-Based Classification with LinkedIn Categories
Analyzes job titles and descriptions using OpenAI to classify them into LinkedIn's standard taxonomy
"""

import os
import json
import re
import logging
import time
from typing import Dict, List, Optional, Any, Union
# the newest OpenAI model is "gpt-5" which was released August 7, 2025.
# do not change this unless explicitly requested by the user
from openai import OpenAI

class AIJobClassifier:
    """AI-powered classifier using OpenAI with LinkedIn's official categories"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Initialize OpenAI client using blueprint:python_openai integration
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required")
        
        self.client = OpenAI(api_key=api_key)
        
        # LinkedIn Job Functions (28 categories) - from linkedin_categories.md
        self.job_functions = [
            "Accounting", "Administrative", "Arts and Design", "Business Development",
            "Community and Social Services", "Consulting", "Customer Success and Support",
            "Education", "Engineering", "Entrepreneurship", "Finance", "Healthcare Services",
            "Human Resources", "Information Technology", "Legal", "Marketing",
            "Media and Communication", "Military and Protective Services", "Operations",
            "Product Management", "Program and Project Management", "Purchasing",
            "Quality Assurance", "Real Estate", "Research", "Sales"
        ]
        
        # LinkedIn Industries (20 categories) - from linkedin_categories.md
        self.industries = [
            "Accommodation Services", "Administrative and Support Services", "Construction",
            "Consumer Services", "Education", "Entertainment Providers",
            "Farming, Ranching, Forestry", "Financial Services", "Government Administration",
            "Holding Companies", "Hospitals and Health Care", "Manufacturing",
            "Oil, Gas, and Mining", "Professional Services",
            "Real Estate and Equipment Rental Services", "Retail",
            "Technology, Information and Media",
            "Transportation, Logistics, Supply Chain and Storage", "Utilities", "Wholesale"
        ]
        
        # LinkedIn Seniority Levels (5 categories) - from linkedin_categories.md
        self.seniority_levels = [
            "Executive", "Director", "Mid-Senior level", "Entry level", "Internship"
        ]
        
        self.logger.info("🤖 AI job classifier initialized with LinkedIn categories (28 functions, 20 industries, 5 seniority levels)")
    
    def classify_job(self, title: str, description: str) -> Dict[str, Any]:
        """
        Classify a job using AI (OpenAI GPT-5) with LinkedIn's official taxonomy
        
        Args:
            title: Job title
            description: Job description (may contain HTML)
            
        Returns:
            Dict with 'success', 'job_function', 'industries', 'seniority_level'
        """
        try:
            # Clean description (remove HTML tags)
            clean_desc = re.sub(r'<[^>]+>', ' ', description) if description else ""
            clean_desc = re.sub(r'\s+', ' ', clean_desc).strip()
            
            # Truncate if too long (keep first 1000 chars for context)
            if len(clean_desc) > 1000:
                clean_desc = clean_desc[:1000] + "..."
            
            # Build prompt with LinkedIn categories
            prompt = f"""Analyze this job posting and classify it using LinkedIn's standard taxonomy.

Job Title: {title}
Job Description: {clean_desc}

You must select exactly ONE category from each list below:

JOB FUNCTIONS (28 official LinkedIn categories):
{', '.join(self.job_functions)}

INDUSTRIES (20 official LinkedIn categories):
{', '.join(self.industries)}

SENIORITY LEVELS (5 official LinkedIn categories):
{', '.join(self.seniority_levels)}

Respond with JSON in this exact format:
{{
  "job_function": "one of the job functions above",
  "industry": "one of the industries above",
  "seniority_level": "one of the seniority levels above"
}}

CRITICAL REQUIREMENTS - READ CAREFULLY:
1. You MUST use ONLY the EXACT category names from the lists above
2. Do NOT create ANY new categories or variations
3. Do NOT modify the category names in any way
4. Do NOT combine categories or use multiple categories
5. Choose the SINGLE BEST match from each list
6. If uncertain, pick the CLOSEST match from the provided lists
7. Copy the category name EXACTLY as shown (including capitalization)

Context-specific guidance:
- For estimator roles in construction/energy: use "Operations" or "Administrative" function
- For construction/substation jobs: use "Construction" or "Oil, Gas, and Mining" industry
- For software/IT roles: use "Information Technology" function
- For consulting roles: use "Consulting" function"""

            # Call OpenAI API
            # Note: gpt-5 uses reasoning tokens internally, so we need higher max_completion_tokens
            # to allow for both reasoning (500 tokens) and actual output (500 tokens)
            response = self.client.chat.completions.create(
                model="gpt-5",
                messages=[
                    {"role": "system", "content": "You are an expert job classifier using LinkedIn's official taxonomy. Always respond with valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                max_completion_tokens=1500  # Increased to allow for reasoning + output
            )
            
            # Parse response with defensive checks
            if not response.choices or len(response.choices) == 0:
                self.logger.error(f"OpenAI returned no choices for job: {title}")
                raise ValueError("No choices in OpenAI API response")
            
            message = response.choices[0].message
            if not message:
                self.logger.error(f"OpenAI returned no message for job: {title}")
                raise ValueError("No message in OpenAI API response")
            
            result_text = message.content
            if not result_text or not result_text.strip():
                self.logger.error(f"OpenAI returned empty content for job: {title}")
                self.logger.error(f"Full response object: {response}")
                raise ValueError("Empty content from OpenAI API")
            
            self.logger.debug(f"OpenAI response: {result_text[:200]}")
            result = json.loads(result_text)
            
            # Validate and extract
            job_function = result.get('job_function', '')
            industry = result.get('industry', '')
            seniority_level = result.get('seniority_level', '')
            
            # Validate against allowed categories
            if job_function not in self.job_functions:
                self.logger.warning(f"AI returned invalid job function: {job_function}")
                job_function = "Information Technology"  # Fallback
            
            if industry not in self.industries:
                self.logger.warning(f"AI returned invalid industry: {industry}")
                industry = "Technology, Information and Media"  # Fallback
            
            if seniority_level not in self.seniority_levels:
                self.logger.warning(f"AI returned invalid seniority: {seniority_level}")
                seniority_level = "Mid-Senior level"  # Fallback
            
            self.logger.debug(f"AI classified '{title}': Function={job_function}, Industry={industry}, Seniority={seniority_level}")
            
            return {
                'success': True,
                'job_function': job_function,
                'industries': industry,
                'seniority_level': seniority_level
            }
            
        except Exception as e:
            self.logger.error(f"AI classification failed for '{title}': {str(e)}")
            return {
                'success': False,
                'job_function': '',
                'industries': '',
                'seniority_level': '',
                'error': str(e)
            }


class InternalJobClassifier:
    """Sophisticated keyword-based classifier - fast, reliable, deterministic"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Comprehensive keyword mappings for ALL 28 LinkedIn job functions
        self.function_keywords = {
            'Accounting': ['accountant', 'cpa', 'bookkeeper', 'accounting', 'accounts payable', 'accounts receivable', 'general ledger', 'payroll'],
            'Administrative': ['admin', 'administrative', 'office manager', 'receptionist', 'secretary', 'clerk', 'coordinator', 'scheduler', 'assistant'],
            'Arts and Design': ['designer', 'artist', 'creative', 'graphic', 'ux', 'ui', 'illustrator', 'animator', 'art director'],
            'Business Development': ['business development', 'partnership', 'growth', 'strategy', 'biz dev', 'expansion'],
            'Community and Social Services': ['social worker', 'counselor', 'community', 'nonprofit', 'advocate', 'case manager', 'therapist'],
            'Consulting': ['consultant', 'consulting', 'advisory', 'advisor', 'strategist'],
            'Customer Success and Support': ['customer success', 'customer support', 'help desk', 'support specialist', 'service representative'],
            'Education': ['teacher', 'educator', 'professor', 'instructor', 'trainer', 'tutor', 'academic', 'curriculum'],
            'Engineering': ['engineer', 'engineering', 'civil', 'mechanical', 'electrical', 'structural', 'technical design', 'cad'],
            'Entrepreneurship': ['founder', 'entrepreneur', 'startup', 'ceo', 'co-founder', 'owner'],
            'Finance': ['financial analyst', 'finance', 'investment', 'treasury', 'fp&a', 'controller', 'cfo'],
            'Healthcare Services': ['nurse', 'physician', 'medical', 'healthcare', 'clinical', 'doctor', 'practitioner', 'therapist'],
            'Human Resources': ['hr', 'human resources', 'recruiter', 'talent acquisition', 'benefits', 'compensation', 'people operations'],
            'Information Technology': ['developer', 'programmer', 'software', 'it', 'tech', 'systems', 'network', 'database', 'devops', 'sysadmin'],
            'Legal': ['attorney', 'lawyer', 'legal', 'counsel', 'paralegal', 'compliance', 'contract'],
            'Marketing': ['marketing', 'brand', 'digital marketing', 'content', 'seo', 'sem', 'social media', 'campaigns'],
            'Media and Communication': ['journalist', 'writer', 'editor', 'communications', 'public relations', 'pr', 'media', 'content creator'],
            'Military and Protective Services': ['security', 'military', 'police', 'guard', 'protective', 'defense', 'law enforcement'],
            'Operations': ['operations', 'logistics', 'supply chain', 'ops', 'procurement', 'facilities', 'estimator', 'project controls'],
            'Product Management': ['product manager', 'product owner', 'product lead', 'roadmap', 'product strategy'],
            'Program and Project Management': ['project manager', 'program manager', 'scrum master', 'pmo', 'project lead', 'agile'],
            'Purchasing': ['buyer', 'purchasing', 'procurement', 'sourcing', 'vendor'],
            'Quality Assurance': ['qa', 'quality', 'tester', 'quality assurance', 'quality control', 'qc', 'test engineer'],
            'Real Estate': ['real estate', 'property', 'leasing', 'realtor', 'broker', 'property manager'],
            'Research': ['researcher', 'scientist', 'analyst', 'data scientist', 'research'],
            'Sales': ['sales', 'account executive', 'account manager', 'sales rep', 'business development rep', 'bdr', 'ae']
        }
        
        # Comprehensive keyword mappings for ALL 20 LinkedIn industries
        self.industry_keywords = {
            'Accommodation Services': ['hotel', 'hospitality', 'lodging', 'resort', 'motel', 'inn'],
            'Administrative and Support Services': ['staffing', 'temp', 'recruiting', 'outsourcing', 'bpo', 'call center'],
            'Construction': ['construction', 'building', 'contractor', 'subcontractor', 'general contractor', 'trades', 'substation', 'infrastructure'],
            'Consumer Services': ['consumer', 'retail services', 'personal services', 'laundry', 'repair'],
            'Education': ['education', 'school', 'university', 'college', 'k-12', 'training', 'e-learning'],
            'Entertainment Providers': ['entertainment', 'gaming', 'casino', 'amusement', 'recreation', 'media production'],
            'Farming, Ranching, Forestry': ['agriculture', 'farming', 'ranch', 'forestry', 'agricultural', 'crop'],
            'Financial Services': ['financial services', 'banking', 'investment', 'wealth management', 'insurance', 'fintech'],
            'Government Administration': ['government', 'federal', 'state', 'municipal', 'public sector', 'agency'],
            'Holding Companies': ['holding company', 'conglomerate', 'parent company', 'investment holding'],
            'Hospitals and Health Care': ['hospital', 'healthcare', 'medical', 'clinic', 'health system', 'patient care'],
            'Manufacturing': ['manufacturing', 'factory', 'production', 'assembly', 'industrial'],
            'Oil, Gas, and Mining': ['oil', 'gas', 'petroleum', 'energy', 'mining', 'extraction', 'refinery', 'substation', 'power'],
            'Professional Services': ['consulting', 'professional services', 'advisory', 'legal services', 'accounting firm'],
            'Real Estate and Equipment Rental Services': ['real estate', 'property management', 'leasing', 'rental', 'equipment rental'],
            'Retail': ['retail', 'store', 'shop', 'ecommerce', 'e-commerce', 'merchandise'],
            'Technology, Information and Media': ['software', 'technology', 'tech', 'saas', 'it services', 'digital', 'internet', 'cloud'],
            'Transportation, Logistics, Supply Chain and Storage': ['transportation', 'logistics', 'supply chain', 'shipping', 'warehousing', 'freight', 'trucking'],
            'Utilities': ['utilities', 'electric', 'water', 'gas utility', 'power utility', 'energy utility'],
            'Wholesale': ['wholesale', 'distribution', 'distributor', 'b2b sales']
        }
        
        self.logger.info("🔧 Enhanced keyword classifier initialized (28 functions, 20 industries, 5 seniority levels)")
    
    def classify_job(self, title: str, description: str) -> Dict[str, Any]:
        """Sophisticated keyword-based classification with scoring algorithm"""
        title_lower = title.lower().strip()
        desc_lower = description.lower().strip() if description else ""
        combined = f"{title_lower} {desc_lower}"
        
        # Find best function (title keywords weighted 3x higher than description)
        job_function = "Operations"  # Neutral default
        best_score = 0
        for func, keywords in self.function_keywords.items():
            score = sum(3 if kw in title_lower else (1 if kw in desc_lower else 0) for kw in keywords)
            if score > best_score:
                best_score = score
                job_function = func
        
        # Find best industry (title keywords weighted 3x higher than description)
        industry = "Professional Services"  # Neutral default
        best_score = 0
        for ind, keywords in self.industry_keywords.items():
            score = sum(3 if kw in title_lower else (1 if kw in desc_lower else 0) for kw in keywords)
            if score > best_score:
                best_score = score
                industry = ind
        
        # Determine seniority with comprehensive keyword matching
        seniority = "Mid-Senior level"  # Default for most positions
        if any(kw in title_lower for kw in ['ceo', 'cto', 'cfo', 'chief', 'president', 'vp', 'vice president', 'executive']):
            seniority = "Executive"
        elif any(kw in title_lower for kw in ['director', 'head of', 'managing']):
            seniority = "Director"
        elif any(kw in title_lower for kw in ['senior', 'sr.', 'sr ', 'lead', 'principal', 'staff']):
            seniority = "Mid-Senior level"
        elif any(kw in title_lower for kw in ['junior', 'jr.', 'jr ', 'entry', 'associate', 'assistant']):
            seniority = "Entry level"
        elif any(kw in title_lower for kw in ['intern', 'internship', 'co-op']):
            seniority = "Internship"
        
        return {
            'success': True,
            'job_function': job_function,
            'industries': industry,
            'seniority_level': seniority
        }


class JobClassificationService:
    """Service for classifying jobs using AI-first approach with keyword fallback"""
    
    def __init__(self, use_ai: bool = True):
        """
        Initialize job classification service
        
        Args:
            use_ai: If True, use AI classification (default). If False, use keyword fallback.
        """
        self.logger = logging.getLogger(__name__)
        self.use_ai = use_ai
        
        # Initialize classifiers
        try:
            if use_ai:
                self.ai_classifier = AIJobClassifier()
                self.logger.info("🤖 AI-based classifier active (primary)")
            else:
                self.ai_classifier = None
                self.logger.info("⚡ AI classification disabled, using keywords only")
        except Exception as e:
            self.logger.warning(f"Failed to initialize AI classifier: {e}")
            self.ai_classifier = None
            self.use_ai = False
        
        # Always have fallback classifier
        self.fallback_classifier = InternalJobClassifier()
        self.logger.info("🔧 Keyword fallback classifier ready")
    
    def classify_job(self, job_title: str, job_description: str) -> Dict[str, str]:
        """
        Classify a single job using AI (with fallback to keywords)
        
        Args:
            job_title: The job title
            job_description: The job description (can include HTML)
            
        Returns:
            Dict with 'job_function', 'industries', and 'seniority_level'
        """
        # Try AI first if enabled
        if self.use_ai and self.ai_classifier:
            result = self.ai_classifier.classify_job(job_title, job_description)
            if result.get('success'):
                return result
            else:
                self.logger.warning(f"AI classification failed, using fallback for: {job_title}")
        
        # Fallback to keyword classification
        return self.fallback_classifier.classify_job(job_title, job_description)
    
    def classify_jobs_batch(self, jobs: List[Dict[str, str]], batch_size: int = 4, 
                          max_retries: int = 2, max_processing_time: Optional[int] = None) -> List[Dict[str, str]]:
        """
        Classify multiple jobs in batches with timeout protection
        
        Args:
            jobs: List of dicts with 'title' and 'description' keys
            batch_size: Number of jobs to process in parallel (default: 4 for AI)
            max_retries: Number of retries for failed classifications
            max_processing_time: Maximum time in seconds (optional timeout)
            
        Returns:
            List of classification results in same order as input
        """
        start_time = time.time()
        results = []
        
        self.logger.info(f"🚀 Classifying {len(jobs)} jobs (AI={'enabled' if self.use_ai else 'disabled'}, batch_size={batch_size})")
        
        for i, job in enumerate(jobs):
            # Check timeout if specified
            if max_processing_time:
                elapsed = time.time() - start_time
                if elapsed > max_processing_time:
                    self.logger.warning(f"⏰ Batch classification timeout after {elapsed:.1f}s at job {i+1}/{len(jobs)}")
                    self.logger.info(f"🔄 Using keyword fallback for remaining {len(jobs) - i} jobs to prevent blank fields")
                    # Fill remaining with keyword fallback results instead of empty results
                    for remaining_job in jobs[i:]:
                        fallback_result = self.fallback_classifier.classify_job(
                            remaining_job['title'], 
                            remaining_job['description']
                        )
                        results.append(fallback_result)
                    break
            
            # Classify individual job with retry logic
            retry_count = 0
            while retry_count <= max_retries:
                try:
                    result = self.classify_job(job['title'], job['description'])
                    results.append(result)
                    break
                except Exception as e:
                    retry_count += 1
                    if retry_count > max_retries:
                        self.logger.error(f"Classification failed after {max_retries} retries for '{job['title']}': {e}")
                        results.append({
                            'success': False,
                            'job_function': '',
                            'industries': '',
                            'seniority_level': '',
                            'error': str(e)
                        })
                    else:
                        self.logger.warning(f"Retry {retry_count}/{max_retries} for '{job['title']}'")
                        time.sleep(1)  # Brief delay before retry
        
        elapsed_total = time.time() - start_time
        success_count = sum(1 for r in results if r.get('success'))
        self.logger.info(f"✅ Batch classification completed: {success_count}/{len(jobs)} successful in {elapsed_total:.1f}s")
        
        return results
