import re
from typing import Optional


PRESTIGE_FIRMS = [
    'deloitte', 'pwc', 'pricewaterhousecoopers', 'ernst & young', 'ernst and young',
    'kpmg',
    'accenture', 'mckinsey', 'bain & company', 'bain and company', 'boston consulting group',
    'infosys', 'wipro', 'tata consultancy', 'tata consultancy services', 'cognizant', 'capgemini',
    'ibm consulting', 'booz allen', 'booz allen hamilton',
    'hcl technologies', 'hcltech', 'tech mahindra', 'lti mindtree', 'ltimindtree',
    'ntt data', 'dxc technology', 'unisys', 'atos',
    'slalom', 'thoughtworks', 'publicis sapient', 'epam',
    'cgi group', 'cgi inc',
]

PRESTIGE_DISPLAY_NAMES = {
    'ernst & young': 'EY (Ernst & Young)',
    'ernst and young': 'EY (Ernst & Young)',
    'pricewaterhousecoopers': 'PwC',
    'tata consultancy': 'TCS (Tata Consultancy Services)',
    'tata consultancy services': 'TCS (Tata Consultancy Services)',
    'bain & company': 'Bain & Company',
    'bain and company': 'Bain & Company',
    'boston consulting group': 'BCG (Boston Consulting Group)',
    'booz allen hamilton': 'Booz Allen Hamilton',
    'booz allen': 'Booz Allen Hamilton',
    'cgi group': 'CGI',
    'cgi inc': 'CGI',
    'hcl technologies': 'HCL Technologies',
    'lti mindtree': 'LTIMindtree',
    'ltimindtree': 'LTIMindtree',
}

PRESTIGE_BOOST_POINTS = 5


def detect_prestige_employer(resume_text: str) -> Optional[str]:
    if not resume_text:
        return None
    resume_lower = resume_text.lower()
    lines = resume_lower.split('\n')
    top_section = '\n'.join(lines[:max(len(lines) // 3, 40)])

    for section in [top_section, resume_lower]:
        for firm in PRESTIGE_FIRMS:
            pattern = r'(?<![a-z])' + re.escape(firm) + r'(?![a-z])'
            if re.search(pattern, section):
                return PRESTIGE_DISPLAY_NAMES.get(firm, firm.title())
        if section is top_section:
            continue
    return None
