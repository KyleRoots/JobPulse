import re
from typing import Optional, Sequence


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

# Explicit telecom / satellite operators. Longer aliases first at match time.
# Canada is first-class (Telesat, Bell, Rogers, TELUS, and regional MNOs).
# Short generic tokens (bell, shaw, cox, ses, bt, orange) are not used alone.
TELECOM_SATELLITE_FIRMS = [
    # Canada: satellite
    'telesat lightspeed', 'telesat canada', 'telesat',
    'mda space', 'macdonald, dettwiler', 'macdonald dettwiler',
    'kepler communications', 'c-com satellite',
    'northstar earth & space', 'northstar earth and space',
    # Canada: national / regional telecom
    'bell canada', 'bell mobility', 'bell aliant', 'bell mts', 'bce inc.',
    'bce inc', 'rogers communications', 'rogers wireless', 'rogers telecom',
    'telus communications', 'telus mobility', 'telus',
    'shaw communications', 'shaw cable', 'shaw direct',
    'videotron', 'quebecor', 'freedom mobile',
    'cogeco', 'eastlink', 'sasktel', 'xplornet', 'xplore mobile',
    'fido', 'koodo', 'public mobile', 'virgin plus',
    # US / global operators and satellite
    'verizon', 'at&t', 't-mobile', 'comcast', 'charter communications',
    'cox communications', 'dish network', 'echostar',
    'hughes network', 'hughesnet', 'viasat', 'intelsat', 'eutelsat',
    'inmarsat', 'iridium', 'globalstar', 'oneweb', 'starlink', 'spacex',
    'ast spacemobile', 'lumen technologies', 'centurylink',
    'frontier communications', 'bt group', 'british telecom', 'vodafone',
    'deutsche telekom', 'telefonica', 'ntt docomo', 'singtel', 'telstra',
    'ses s.a', 'ses world skies',
]

TELECOM_SATELLITE_DISPLAY_NAMES = {
    'telesat lightspeed': 'Telesat',
    'telesat canada': 'Telesat',
    'mda space': 'MDA Space',
    'macdonald, dettwiler': 'MDA Space',
    'macdonald dettwiler': 'MDA Space',
    'kepler communications': 'Kepler Communications',
    'c-com satellite': 'C-COM Satellite Systems',
    'northstar earth & space': 'NorthStar Earth & Space',
    'northstar earth and space': 'NorthStar Earth & Space',
    'bell canada': 'Bell Canada',
    'bell mobility': 'Bell Canada',
    'bell aliant': 'Bell Canada',
    'bell mts': 'Bell Canada',
    'bce inc.': 'Bell Canada (BCE)',
    'bce inc': 'Bell Canada (BCE)',
    'rogers communications': 'Rogers',
    'rogers wireless': 'Rogers',
    'rogers telecom': 'Rogers',
    'telus communications': 'TELUS',
    'telus mobility': 'TELUS',
    'telus': 'TELUS',
    'shaw communications': 'Shaw Communications',
    'shaw cable': 'Shaw Communications',
    'shaw direct': 'Shaw Direct',
    'quebecor': 'Quebecor',
    'freedom mobile': 'Freedom Mobile',
    'sasktel': 'SaskTel',
    'xplornet': 'Xplore',
    'xplore mobile': 'Xplore',
    'fido': 'Fido (Rogers)',
    'koodo': 'Koodo (TELUS)',
    'public mobile': 'Public Mobile (TELUS)',
    'virgin plus': 'Virgin Plus (Bell)',
    'at&t': 'AT&T',
    't-mobile': 'T-Mobile',
    'charter communications': 'Charter Communications',
    'cox communications': 'Cox Communications',
    'dish network': 'Dish Network',
    'hughes network': 'Hughes Network Systems',
    'hughesnet': 'HughesNet',
    'ast spacemobile': 'AST SpaceMobile',
    'lumen technologies': 'Lumen',
    'centurylink': 'Lumen (CenturyLink)',
    'frontier communications': 'Frontier',
    'bt group': 'BT Group',
    'british telecom': 'BT Group',
    'deutsche telekom': 'Deutsche Telekom',
    'ntt docomo': 'NTT Docomo',
    'ses s.a': 'SES',
    'ses world skies': 'SES',
    'spacex': 'SpaceX',
    'starlink': 'Starlink (SpaceX)',
}

PRESTIGE_BOOST_POINTS = 5


def _detect_from_list(
    resume_text: str,
    firms: Sequence[str],
    display_names: dict,
) -> Optional[str]:
    if not resume_text:
        return None
    resume_lower = resume_text.lower()
    lines = resume_lower.split('\n')
    top_section = '\n'.join(lines[:max(len(lines) // 3, 40)])
    ordered = sorted(firms, key=len, reverse=True)

    for section in [top_section, resume_lower]:
        for firm in ordered:
            pattern = r'(?<![a-z0-9])' + re.escape(firm) + r'(?![a-z0-9])'
            if re.search(pattern, section):
                return display_names.get(firm, firm.title())
    return None


def detect_prestige_employer(resume_text: str) -> Optional[str]:
    return _detect_from_list(resume_text, PRESTIGE_FIRMS, PRESTIGE_DISPLAY_NAMES)


def detect_telecom_satellite_employer(resume_text: str) -> Optional[str]:
    return _detect_from_list(
        resume_text, TELECOM_SATELLITE_FIRMS, TELECOM_SATELLITE_DISPLAY_NAMES
    )


def resolve_employer_boost(
    consulting_employer: Optional[str],
    telecom_employer: Optional[str],
    consulting_enabled: bool,
    telecom_enabled: bool,
) -> Optional[str]:
    """Return the employer name that should receive the +5 boost, or None.

    Consulting and telecom checkboxes are independent. Points do not stack.
    """
    if consulting_enabled and consulting_employer:
        return consulting_employer
    if telecom_enabled and telecom_employer:
        return telecom_employer
    return None
