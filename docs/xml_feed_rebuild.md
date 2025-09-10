# XML Feed Rebuild Documentation

## Overview
The XML feed system has been rebuilt to provide a cleaner, more maintainable architecture for generating job feeds from Bullhorn data. The new system follows a modular approach with clear separation of concerns.

## Architecture

### Core Components

1. **feeds/myticas_v2.py** - Main feed generator
   - `build_myticas_feed()` - Builds XML from job data
   - `validate_myticas_feed()` - Validates against expected structure
   - `publish()` - Uploads to SFTP server

2. **feeds/tearsheet_flow.py** - Tearsheet integration
   - Manages tearsheet-driven rebuilds
   - Handles debouncing and rate limiting
   - Integrates with Bullhorn API

3. **feeds/freeze_manager.py** - Freeze mechanism
   - Controls when XML operations are allowed
   - Provides audit logging
   - Sends alerts when operations are blocked

## Data Flow

```
Bullhorn API → Tearsheet Flow → Feed Generator → Validation → SFTP Upload
                                       ↓
                                AI Classification
                                (jobfunction, jobindustries, senioritylevel)
```

## Configuration

### Environment Variables

- `XML_FEED_FRZ` - Set to 'true' to freeze all XML operations
- `APPLY_EMAIL` - Email address for job applications (default: apply@myticas.com)
- `PUBLIC_JOB_URL_BASE` - Base URL for job application links
- `XML_ALERTS_EMAIL` - Email for system alerts
- `SKIP_AI_CLASSIFICATION` - Set to 'true' to skip AI classification (for testing)

### SFTP Configuration

- `SFTP_HOST` - SFTP server hostname
- `SFTP_USER` - SFTP username
- `SFTP_PASSWORD` - SFTP password
- `SFTP_PORT` - SFTP port (default: 22)
- `SFTP_KEY` - Path to SSH key file (optional)

## Freeze/Unfreeze Process

### To Freeze the System
```bash
# Set the freeze flag
export XML_FEED_FRZ=true
```

This will:
- Disable all scheduled XML rebuilds
- Prevent SFTP uploads
- Block manual refresh operations
- Send alerts to XML_ALERTS_EMAIL

### To Unfreeze the System
```bash
# Clear the freeze flag
unset XML_FEED_FRZ
# Or set to false
export XML_FEED_FRZ=false
```

## Validation

### Running Validation Script
```bash
# Validate existing XML file
python scripts/validate_feed.py --validate-only myticas-job-feed-v2.xml

# Generate and validate test feed
python scripts/validate_feed.py --limit 5 --out /tmp/test-feed.xml

# Test with SFTP upload
python scripts/validate_feed.py --test-sftp

# Skip AI classification for faster testing
python scripts/validate_feed.py --skip-ai --limit 10
```

## Tearsheet Configuration

The system monitors 5 live tearsheets:

1. **Open Tech Opportunities (OTT)** - ID: 1234
2. **VMS Active Jobs** - ID: 1267
3. **Sponsored - STSI** - ID: 1556
4. **Grow (GR)** - ID: 1300
5. **Chicago (CHI)** - ID: 1523

Each tearsheet triggers a rebuild when jobs are added, removed, or modified.

## XML Structure

The feed follows this structure:
```xml
<source>
  <title>Myticas Consulting</title>
  <link>https://www.myticas.com</link>
  <job>
    <title><![CDATA[ Job Title (12345) ]]></title>
    <date>2025-01-14</date>
    <referencenumber>ABC123DEF4</referencenumber>
    <bhatsid>12345</bhatsid>
    <company>Myticas Consulting</company>
    <url>https://apply.myticas.com/12345/Job%20Title/?source=LinkedIn</url>
    <description><![CDATA[ Job description... ]]></description>
    <jobtype>Contract</jobtype>
    <city>Chicago</city>
    <state>IL</state>
    <country>United States</country>
    <category></category>
    <apply_email>apply@myticas.com</apply_email>
    <remotetype>Hybrid</remotetype>
    <assignedrecruiter>John Doe</assignedrecruiter>
    <jobfunction>Information Technology</jobfunction>
    <jobindustries>Technology</jobindustries>
    <senioritylevel>Mid-Senior level</senioritylevel>
  </job>
</source>
```

## Field Mappings

| Bullhorn Field | XML Field | Source | Notes |
|----------------|-----------|--------|-------|
| id | bhatsid | Bullhorn API | Job ID |
| title | title | Bullhorn API | Formatted with ID |
| publicDescription | description | Bullhorn API | CDATA wrapped |
| employmentType | jobtype | Bullhorn API | Mapped values |
| address.city | city | Bullhorn API | |
| address.state | state | Bullhorn API | |
| address.countryName | country | Bullhorn API | |
| onSite | remotetype | Bullhorn API | Remote/Hybrid/On-site |
| assignedUsers | assignedrecruiter | Bullhorn API | |
| dateAdded | date | Bullhorn API | Formatted YYYY-MM-DD |
| - | referencenumber | Generated | 10-char unique ID |
| - | url | Generated | Based on company |
| - | apply_email | Config | From APPLY_EMAIL |
| - | jobfunction | AI | GPT-4o classification |
| - | jobindustries | AI | GPT-4o classification |
| - | senioritylevel | AI | GPT-4o classification |

## Monitoring

The system includes comprehensive monitoring:

1. **Tearsheet Monitoring** - Every 5 minutes
2. **Field Validation** - Ensures data integrity
3. **Upload Verification** - Confirms SFTP success
4. **Change Detection** - Tracks additions/removals/modifications

## Troubleshooting

### Common Issues

1. **Feed not updating**
   - Check if system is frozen: `echo $XML_FEED_FRZ`
   - Verify Bullhorn credentials are set
   - Check SFTP configuration

2. **Validation failures**
   - Run validation script to identify issues
   - Check for missing required fields
   - Verify XML syntax

3. **SFTP upload failures**
   - Verify SFTP credentials
   - Check network connectivity
   - Ensure correct permissions on server

### Debug Commands

```bash
# Check freeze status
python -c "from feeds.freeze_manager import FreezeManager; print(FreezeManager().get_status())"

# Test Bullhorn connection
python -c "from bullhorn_service import BullhornService; bs = BullhornService(); print(bs.test_connection())"

# Check SFTP configuration
python -c "from feeds.tearsheet_flow import TearsheetFlow; tf = TearsheetFlow(); print(tf._get_sftp_config())"
```

## Migration Notes

### From Legacy System

1. The new system preserves existing reference numbers during migration
2. AI-generated fields are maintained from the old system
3. URL format remains compatible with existing job application forms
4. SFTP upload path remains the same (/myticas-job-feed-v2.xml)

### Rollback Plan

If rollback is needed:
1. Set `XML_FEED_FRZ=true` to freeze new system
2. Re-enable legacy monitoring in app.py
3. Remove freeze flag when legacy system is confirmed working

## Performance

- Feed generation: ~10-15 seconds for 60 jobs
- AI classification: ~1-2 seconds per job (when not cached)
- SFTP upload: ~2-3 seconds
- Total cycle time: Under 30 seconds for typical load

## Security

- Bullhorn credentials stored as environment variables
- SFTP uses password or SSH key authentication
- No secrets stored in code or logs
- AI API keys managed securely

## Future Enhancements

- [ ] Implement caching for AI classifications
- [ ] Add metrics collection and dashboards
- [ ] Support for multiple output formats
- [ ] Webhook notifications for changes
- [ ] Automated testing pipeline