"""
Tearsheet Configuration Module
Manages mapping between Bullhorn tearsheets and their corresponding XML values
"""

class TearsheetConfig:
    """Configuration for tearsheet-specific XML values"""
    
    # Tearsheet mapping configuration
    TEARSHEET_MAPPINGS = {
        # Default Myticas tearsheet (existing)
        'default': {
            'company_name': 'Myticas Consulting',
            'domain': 'apply.myticas.com',
            'logo': '/static/images/myticas_logo.png',
            'primary_color': '#1a1a1a',  # Dark theme
            'secondary_color': '#f8f9fa',
            'brand_name': 'Myticas'
        },
        
        # New STSI tearsheet
        'Sponsored - STSI': {
            'company_name': 'STSI (Staffing Technical Services Inc.)',
            'domain': 'apply.stsigroup.com',
            'logo': '/static/images/stsi_logo.png',
            'primary_color': '#00B5B5',  # Teal from STSI branding
            'secondary_color': '#6c757d',  # Gray
            'brand_name': 'STSI (Staffing Technical Services Inc.)'
        },
        
        # Fallback for any tearsheet not explicitly defined
        'fallback': {
            'company_name': 'Myticas Consulting',
            'domain': 'apply.myticas.com',
            'logo': '/static/images/myticas_logo.png',
            'primary_color': '#1a1a1a',
            'secondary_color': '#f8f9fa',
            'brand_name': 'Myticas'
        }
    }
    
    @classmethod
    def get_config_for_tearsheet(cls, tearsheet_name):
        """
        Get configuration for a specific tearsheet
        
        Args:
            tearsheet_name: Name of the tearsheet from Bullhorn
            
        Returns:
            dict: Configuration for the tearsheet
        """
        # Check if we have a specific mapping for this tearsheet
        if tearsheet_name in cls.TEARSHEET_MAPPINGS:
            return cls.TEARSHEET_MAPPINGS[tearsheet_name]
        
        # Check if tearsheet name contains "STSI" (case-insensitive)
        if tearsheet_name and 'stsi' in tearsheet_name.lower():
            return cls.TEARSHEET_MAPPINGS['Sponsored - STSI']
        
        # Return default Myticas configuration
        return cls.TEARSHEET_MAPPINGS['fallback']
    
    @classmethod
    def get_company_name(cls, tearsheet_name):
        """Get company name for a tearsheet"""
        config = cls.get_config_for_tearsheet(tearsheet_name)
        return config['company_name']
    
    @classmethod
    def get_application_url(cls, tearsheet_name, job_id, job_title):
        """
        Generate application URL based on tearsheet
        
        Args:
            tearsheet_name: Name of the tearsheet
            job_id: Bullhorn job ID
            job_title: Job title for URL encoding
            
        Returns:
            str: Full application URL
        """
        import urllib.parse
        
        config = cls.get_config_for_tearsheet(tearsheet_name)
        domain = config['domain']
        
        # URL encode the job title, replacing problematic characters
        safe_title = job_title.replace('/', '-').replace('\\', '-')
        encoded_title = urllib.parse.quote(safe_title)
        
        # Generate URL with LinkedIn source parameter
        return f"https://{domain}/{job_id}/{encoded_title}/?source=LinkedIn"
    
    @classmethod
    def get_branding_for_domain(cls, domain):
        """
        Get branding configuration based on the domain being accessed
        
        Args:
            domain: The domain from the request (e.g., 'apply.myticas.com')
            
        Returns:
            dict: Branding configuration for the domain
        """
        # Find matching configuration based on domain
        for config in cls.TEARSHEET_MAPPINGS.values():
            if config.get('domain') == domain:
                return config
        
        # Check if it's an STSI domain
        if 'stsigroup' in domain.lower():
            return cls.TEARSHEET_MAPPINGS['Sponsored - STSI']
        
        # Default to Myticas branding
        return cls.TEARSHEET_MAPPINGS['fallback']