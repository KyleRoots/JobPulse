"""
Geographic utility functions for candidate vetting.

Extracted from CandidateVettingService - these are pure functions
that take string arguments and return strings with no class state dependency.
"""


def map_work_type(onsite_value) -> str:
    """
    Map Bullhorn onSite value to work type string.
    Handles both numeric (1, 2, 3) and string ('Remote', 'On-Site', 'Hybrid') values.
    """
    # Handle list format
    if isinstance(onsite_value, list):
        onsite_value = onsite_value[0] if onsite_value else 1
    
    # Handle numeric values
    if isinstance(onsite_value, (int, float)):
        work_type_map = {1: 'On-site', 2: 'Hybrid', 3: 'Remote'}
        return work_type_map.get(int(onsite_value), 'On-site')
    
    # Handle string values
    if onsite_value:
        onsite_str = str(onsite_value).lower().strip()
        if 'remote' in onsite_str or onsite_str == 'offsite':
            return 'Remote'
        elif 'hybrid' in onsite_str:
            return 'Hybrid'
        elif 'on-site' in onsite_str or 'onsite' in onsite_str or onsite_str == 'on site':
            return 'On-site'
    
    # Default to On-site
    return 'On-site'


def normalize_country(country_value: str) -> str:
    """Normalize country names/codes to consistent format for comparison."""
    if not country_value:
        return ''
    country_upper = country_value.strip().upper()
    country_map = {
        # United States variations
        'US': 'United States', 'USA': 'United States', 'U.S.': 'United States',
        'U.S.A.': 'United States', 'UNITED STATES': 'United States',
        'UNITED STATES OF AMERICA': 'United States',
        # Canada variations
        'CA': 'Canada', 'CAN': 'Canada', 'CANADA': 'Canada',
        'CDN': 'Canada', 'CANADIAN': 'Canada',
        # United Kingdom variations
        'UK': 'United Kingdom', 'GB': 'United Kingdom', 'GBR': 'United Kingdom',
        'UNITED KINGDOM': 'United Kingdom', 'GREAT BRITAIN': 'United Kingdom',
        'ENGLAND': 'United Kingdom',
        # India variations
        'IN': 'India', 'IND': 'India', 'INDIA': 'India',
        # Australia variations
        'AU': 'Australia', 'AUS': 'Australia', 'AUSTRALIA': 'Australia',
        # Germany variations
        'DE': 'Germany', 'DEU': 'Germany', 'GERMANY': 'Germany',
        # Mexico variations
        'MX': 'Mexico', 'MEX': 'Mexico', 'MEXICO': 'Mexico',
        # Brazil variations
        'BR': 'Brazil', 'BRA': 'Brazil', 'BRAZIL': 'Brazil',
        # Philippines variations
        'PH': 'Philippines', 'PHL': 'Philippines', 'PHILIPPINES': 'Philippines',
    }
    return country_map.get(country_upper, country_value.strip())


def smart_correct_country(city: str, state: str, declared_country: str) -> str:
    """
    Smart correction for country based on city/state when there's a mismatch.
    This compensates for human data entry errors where candidates or jobs
    have the wrong country but correct city/state.
    
    Returns the corrected country name, or the original if no correction needed.
    """
    if not state and not city:
        return declared_country
    
    state_upper = state.strip().upper() if state else ''
    city_upper = city.strip().upper() if city else ''
    
    # Canadian provinces/territories
    canadian_provinces = {
        'AB', 'BC', 'MB', 'NB', 'NL', 'NS', 'NT', 'NU', 'ON', 'PE', 'QC', 'SK', 'YT',
        'ALBERTA', 'BRITISH COLUMBIA', 'MANITOBA', 'NEW BRUNSWICK', 
        'NEWFOUNDLAND AND LABRADOR', 'NEWFOUNDLAND', 'NOVA SCOTIA',
        'NORTHWEST TERRITORIES', 'NUNAVUT', 'ONTARIO', 'PRINCE EDWARD ISLAND',
        'QUEBEC', 'SASKATCHEWAN', 'YUKON'
    }
    
    # Major Canadian cities (for cases where state might be missing or wrong)
    canadian_cities = {
        'TORONTO', 'MONTREAL', 'VANCOUVER', 'CALGARY', 'EDMONTON', 'OTTAWA',
        'WINNIPEG', 'QUEBEC CITY', 'HAMILTON', 'KITCHENER', 'LONDON', 'VICTORIA',
        'HALIFAX', 'OSHAWA', 'WINDSOR', 'SASKATOON', 'REGINA', 'ST. CATHARINES',
        'KELOWNA', 'BARRIE', 'SHERBROOKE', 'GUELPH', 'KANATA', 'RICHMOND',
        'BURNABY', 'SURREY', 'MARKHAM', 'MISSISSAUGA', 'BRAMPTON', 'SCARBOROUGH',
        'WATERLOO', 'KINGSTON', 'THUNDER BAY', 'SAINT JOHN', 'MONCTON', 'FREDERICTON'
    }
    
    # UK regions/countries
    uk_regions = {
        'ENGLAND', 'SCOTLAND', 'WALES', 'NORTHERN IRELAND',
        'GREATER LONDON', 'WEST MIDLANDS', 'GREATER MANCHESTER'
    }
    
    # Major UK cities
    uk_cities = {
        'LONDON', 'MANCHESTER', 'BIRMINGHAM', 'LEEDS', 'GLASGOW', 'LIVERPOOL',
        'NEWCASTLE', 'SHEFFIELD', 'BRISTOL', 'EDINBURGH', 'CARDIFF', 'BELFAST',
        'NOTTINGHAM', 'LEICESTER', 'COVENTRY', 'BRADFORD', 'READING'
    }
    
    # Australian states
    australian_states = {
        'NSW', 'VIC', 'QLD', 'WA', 'SA', 'TAS', 'ACT', 'NT',
        'NEW SOUTH WALES', 'VICTORIA', 'QUEENSLAND', 'WESTERN AUSTRALIA',
        'SOUTH AUSTRALIA', 'TASMANIA', 'AUSTRALIAN CAPITAL TERRITORY',
        'NORTHERN TERRITORY'
    }
    
    # Mexican states (common abbreviations and names)
    mexican_states = {
        'AGU', 'BCN', 'BCS', 'CAM', 'CHH', 'CHP', 'COA', 'COL', 'DIF', 'DUR',
        'GRO', 'GUA', 'HID', 'JAL', 'MEX', 'MIC', 'MOR', 'NAY', 'NLE', 'OAX',
        'PUE', 'QUE', 'ROO', 'SIN', 'SLP', 'SON', 'TAB', 'TAM', 'TLA', 'VER',
        'YUC', 'ZAC', 'CDMX', 'CIUDAD DE MEXICO', 'JALISCO', 'NUEVO LEON',
        'QUINTANA ROO', 'BAJA CALIFORNIA'
    }
    
    # Major Mexican cities
    mexican_cities = {
        'MEXICO CITY', 'GUADALAJARA', 'MONTERREY', 'PUEBLA', 'TIJUANA',
        'CANCUN', 'LEON', 'JUAREZ', 'MERIDA', 'CHIHUAHUA', 'AGUASCALIENTES',
        'MORELIA', 'QUERETARO', 'TOLUCA', 'HERMOSILLO'
    }
    
    # Check for Canada
    if state_upper in canadian_provinces or city_upper in canadian_cities:
        # Special case: London exists in both Canada (Ontario) and UK
        if city_upper == 'LONDON' and state_upper in {'ENGLAND', 'GREATER LONDON', ''}:
            return 'United Kingdom'
        # If it's a Canadian province or known Canadian city, correct to Canada
        if declared_country != 'Canada':
            return 'Canada'
    
    # Check for UK (but not if state indicates Canada - e.g., London, ON)
    if state_upper in uk_regions or (city_upper in uk_cities and state_upper not in canadian_provinces):
        if declared_country not in ('United Kingdom', 'UK', 'GB'):
            return 'United Kingdom'
    
    # US state abbreviations — used to disambiguate collisions (e.g. WA = Washington vs Western Australia)
    us_states = {
        'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'FL', 'GA',
        'HI', 'ID', 'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD',
        'MA', 'MI', 'MN', 'MS', 'MO', 'MT', 'NE', 'NV', 'NH', 'NJ',
        'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA', 'RI', 'SC',
        'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY', 'DC'
    }
    
    # Check for Australia (skip if state is a known US abbreviation)
    if state_upper in australian_states and state_upper not in us_states:
        if declared_country != 'Australia':
            return 'Australia'
    
    # Check for Mexico
    if state_upper in mexican_states or city_upper in mexican_cities:
        if declared_country != 'Mexico':
            return 'Mexico'
    
    # Egyptian governorates / major cities
    egyptian_cities = {
        'CAIRO', 'ALEXANDRIA', 'GIZA', 'SHUBRA EL KHEIMA', 'PORT SAID',
        'SUEZ', 'LUXOR', 'MANSOURA', 'TANTA', 'ASYUT', 'ISMAILIA',
        'FAIYUM', 'ZAGAZIG', 'ASWAN', 'DAMIETTA', 'MINYA', 'BENI SUEF',
        'HURGHADA', 'SOHAG', '6TH OF OCTOBER CITY', 'NEW CAIRO',
        'HELIOPOLIS', 'MAADI', 'NASR CITY', 'DOKKI', 'MOHANDESSIN',
        'SHARM EL SHEIKH'
    }
    egyptian_governorates = {
        'CAIRO', 'ALEXANDRIA', 'GIZA', 'QALYUBIA', 'DAKAHLIA',
        'SHARQIA', 'GHARBIA', 'MONUFIA', 'BEHEIRA', 'KAFR EL SHEIKH',
        'DAMIETTA', 'PORT SAID', 'ISMAILIA', 'SUEZ', 'NORTH SINAI',
        'SOUTH SINAI', 'RED SEA', 'MATROUH', 'FAIYUM', 'BENI SUEF',
        'MINYA', 'ASYUT', 'SOHAG', 'QENA', 'LUXOR', 'ASWAN',
        'NEW VALLEY'
    }
    
    # Check for Egypt
    if city_upper in egyptian_cities or state_upper in egyptian_governorates:
        if declared_country != 'Egypt':
            return 'Egypt'
    
    # Indian states/territories and major cities
    indian_states = {
        'ANDHRA PRADESH', 'ARUNACHAL PRADESH', 'ASSAM', 'BIHAR',
        'CHHATTISGARH', 'GOA', 'GUJARAT', 'HARYANA', 'HIMACHAL PRADESH',
        'JHARKHAND', 'KARNATAKA', 'KERALA', 'MADHYA PRADESH',
        'MAHARASHTRA', 'MANIPUR', 'MEGHALAYA', 'MIZORAM', 'NAGALAND',
        'ODISHA', 'PUNJAB', 'RAJASTHAN', 'SIKKIM', 'TAMIL NADU',
        'TELANGANA', 'TRIPURA', 'UTTAR PRADESH', 'UTTARAKHAND',
        'WEST BENGAL', 'DELHI', 'NCT OF DELHI', 'NEW DELHI',
        'JAMMU AND KASHMIR', 'LADAKH', 'PUDUCHERRY', 'CHANDIGARH',
        'ANDAMAN AND NICOBAR ISLANDS', 'DADRA AND NAGAR HAVELI',
        'DAMAN AND DIU', 'LAKSHADWEEP'
    }
    indian_cities = {
        'MUMBAI', 'DELHI', 'NEW DELHI', 'BANGALORE', 'BENGALURU',
        'HYDERABAD', 'CHENNAI', 'KOLKATA', 'PUNE', 'AHMEDABAD',
        'JAIPUR', 'LUCKNOW', 'KANPUR', 'NAGPUR', 'INDORE', 'THANE',
        'BHOPAL', 'VISAKHAPATNAM', 'PATNA', 'VADODARA', 'GHAZIABAD',
        'LUDHIANA', 'AGRA', 'NASHIK', 'FARIDABAD', 'MEERUT',
        'RAJKOT', 'VARANASI', 'SRINAGAR', 'AURANGABAD', 'DHANBAD',
        'AMRITSAR', 'ALLAHABAD', 'RANCHI', 'HOWRAH', 'COIMBATORE',
        'JABALPUR', 'GWALIOR', 'VIJAYAWADA', 'JODHPUR', 'MADURAI',
        'RAIPUR', 'KOCHI', 'CHANDIGARH', 'MYSORE', 'MYSURU',
        'NOIDA', 'GREATER NOIDA', 'GURUGRAM', 'GURGAON', 'TRIVANDRUM',
        'THIRUVANANTHAPURAM', 'NAVI MUMBAI', 'MANGALORE'
    }
    
    # Check for India (skip Hyderabad — exists in both India and Pakistan)
    if state_upper in indian_states:
        if declared_country != 'India':
            return 'India'
    if city_upper in indian_cities and city_upper != 'HYDERABAD':
        if declared_country != 'India':
            return 'India'
    
    # Pakistani provinces and major cities
    pakistani_provinces = {
        'SINDH', 'PUNJAB', 'BALOCHISTAN', 'KHYBER PAKHTUNKHWA', 'KPK',
        'ISLAMABAD CAPITAL TERRITORY', 'ICT',
        'GILGIT-BALTISTAN', 'AZAD KASHMIR', 'AJK'
    }
    pakistani_cities = {
        'KARACHI', 'LAHORE', 'ISLAMABAD', 'RAWALPINDI', 'FAISALABAD',
        'MULTAN', 'PESHAWAR', 'QUETTA', 'SIALKOT', 'GUJRANWALA',
        'BAHAWALPUR', 'SARGODHA', 'SUKKUR', 'LARKANA', 'ABBOTTABAD',
        'MARDAN', 'MINGORA', 'DERA GHAZI KHAN', 'MIRPUR', 'NAWABSHAH'
    }
    
    # Check for Pakistan (PUNJAB collision: handled by checking city too)
    if state_upper in pakistani_provinces and state_upper != 'PUNJAB':
        if declared_country != 'Pakistan':
            return 'Pakistan'
    if city_upper in pakistani_cities:
        if declared_country != 'Pakistan':
            return 'Pakistan'
    # Disambiguate PUNJAB: if state is Punjab and city is a known Pakistani city → Pakistan
    if state_upper == 'PUNJAB' and city_upper in pakistani_cities:
        if declared_country != 'Pakistan':
            return 'Pakistan'
    
    # Philippine regions and major cities
    philippine_regions = {
        'NCR', 'NATIONAL CAPITAL REGION', 'METRO MANILA',
        'CALABARZON', 'CENTRAL LUZON', 'WESTERN VISAYAS',
        'CENTRAL VISAYAS', 'DAVAO REGION', 'NORTHERN MINDANAO',
        'ILOCOS REGION', 'BICOL REGION', 'EASTERN VISAYAS',
        'ZAMBOANGA PENINSULA', 'CORDILLERA', 'CAGAYAN VALLEY',
        'CARAGA', 'MIMAROPA', 'SOCCSKSARGEN', 'BARMM'
    }
    philippine_cities = {
        'MANILA', 'QUEZON CITY', 'DAVAO CITY', 'CEBU CITY', 'CEBU',
        'MAKATI', 'TAGUIG', 'PASIG', 'MANDALUYONG', 'CALOOCAN',
        'ZAMBOANGA CITY', 'ANTIPOLO', 'PASAY', 'VALENZUELA',
        'LAS PINAS', 'PARANAQUE', 'MARIKINA', 'MUNTINLUPA',
        'SAN JUAN', 'NAVOTAS', 'MALABON', 'BGC', 'BONIFACIO GLOBAL CITY',
        'ILOILO CITY', 'CAGAYAN DE ORO', 'BACOLOD', 'GENERAL SANTOS',
        'BAGUIO', 'OLONGAPO', 'ANGELES CITY', 'CLARK'
    }
    
    # Check for Philippines
    if state_upper in philippine_regions or city_upper in philippine_cities:
        if declared_country != 'Philippines':
            return 'Philippines'
    
    return declared_country
