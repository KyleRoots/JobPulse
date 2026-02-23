"""
Shared field mapping utilities for Bullhorn-to-XML field transformations.

These functions provide consistent mapping of Bullhorn employment types
and remote/onsite values to XML feed-compatible values. Extracted from
multiple service files to eliminate duplication.
"""

import logging

logger = logging.getLogger(__name__)


def map_employment_type(employment_type: str) -> str:
    """
    Map Bullhorn employment type to XML job type.

    Handles multiple Bullhorn formats:
      - Exact matches via dict lookup (fastest path)
      - Substring matching for non-standard values

    Args:
        employment_type: Raw employment type string from Bullhorn

    Returns:
        Normalized employment type string for XML feed
    """
    if not employment_type:
        return 'Contract'

    # Try exact match first
    exact_mapping = {
        'Contract': 'Contract',
        'Contract-to-Hire': 'Contract to Hire',
        'Direct Hire': 'Direct Hire',
        'Full-Time': 'Direct Hire',
        'Full Time': 'Full-time',
        'Part-Time': 'Part-time',
        'Part Time': 'Part-time',
        'Temporary': 'Contract',
    }
    if employment_type in exact_mapping:
        return exact_mapping[employment_type]

    # Fallback to substring matching for non-standard values
    lower = employment_type.lower()
    if 'contract to hire' in lower or 'contract-to-hire' in lower:
        return 'Contract to Hire'
    elif 'direct' in lower or 'perm' in lower or 'full-time' in lower:
        return 'Direct Hire'
    elif 'contract' in lower:
        return 'Contract'

    return 'Contract'  # Default fallback


def map_remote_type(onsite_value, log_context: str = '') -> str:
    """
    Map Bullhorn onSite value to XML remote type.

    Handles both list and string formats from Bullhorn API.

    Args:
        onsite_value: Raw onSite value from Bullhorn (str or list)
        log_context: Optional context string for debug logging (e.g., job ID)

    Returns:
        Normalized remote type string for XML feed
    """
    if log_context:
        logger.info(f"Mapping onSite value: {onsite_value} (type: {type(onsite_value)}) [{log_context}]")

    # Handle list values (Bullhorn sometimes returns arrays)
    if isinstance(onsite_value, list):
        onsite_value = onsite_value[0] if onsite_value else ''

    # Convert to string and lowercase for comparison
    onsite_str = str(onsite_value).lower() if onsite_value else ''

    if log_context:
        logger.info(f"Processed onSite value for comparison: '{onsite_str}' [{log_context}]")

    # Try exact match first (common Bullhorn values)
    exact_mapping = {
        'remote': 'Remote',
        'on-site': 'Onsite',
        'on site': 'Onsite',
        'onsite': 'Onsite',
        'hybrid': 'Hybrid',
        'no preference': 'No Preference',
        'offsite': 'Remote',
        'off-site': 'Off-Site',
        'off site': 'Off-Site',
    }
    if onsite_str in exact_mapping:
        result = exact_mapping[onsite_str]
    elif 'remote' in onsite_str:
        result = 'Remote'
    elif 'hybrid' in onsite_str:
        result = 'Hybrid'
    elif 'onsite' in onsite_str or 'on-site' in onsite_str or 'on site' in onsite_str:
        result = 'Onsite'
    elif 'off-site' in onsite_str or 'off site' in onsite_str:
        result = 'Off-Site'
    elif onsite_str == '':
        logger.debug(f"Empty onSite value detected - defaulting to Onsite")
        result = 'Onsite'
    else:
        logger.warning(f"Unknown onSite value '{onsite_value}' - defaulting to Onsite")
        result = 'Onsite'

    if log_context:
        logger.info(f"Mapped onSite '{onsite_value}' to remotetype '{result}' [{log_context}]")
    return result
