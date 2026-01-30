# AI Candidate Vetting Module

## Overview

The AI Candidate Vetting Module is an advanced feature of JobPulse™ that automatically analyzes ALL inbound job applicants against open positions using GPT-4o. The system evaluates resumes, scores candidate-job matches based on mandatory requirements, creates detailed notes in Bullhorn for every candidate (qualified and not), and notifies recruiters via collaborative email threads when candidates meet the qualification threshold.

---

## Key Features

### 1. 100% Applicant Coverage
- **ParsedEmail-Based Detection**: Captures ALL inbound applicants by tracking the `vetted_at` column in the ParsedEmail table
- **Both New and Existing Candidates**: Processes applications whether the candidate is new to Bullhorn or already exists with an updated resume
- **Fallback Detection**: Legacy "Online Applicant" status search as backup for candidates entering through other channels

### 2. Intelligent Resume Analysis
- **Resume Extraction**: Downloads resume files (PDF, DOCX, DOC, TXT) from candidate profiles in Bullhorn
- **Text Extraction**: Parses resume content for AI analysis using multiple extraction methods (PyMuPDF, pdfminer, python-docx)
- **AI Matching**: Uses GPT-4o to compare each resume against all active jobs in monitored tearsheets

### 3. Smart Requirement Matching
- **Mandatory Requirements Focus**: AI extracts and focuses ONLY on mandatory job requirements, ignoring "nice-to-have" qualifications
- **Custom Requirements Override**: Administrators can specify custom requirements per job if the AI interpretation is inaccurate
- **Transparent Scoring**: Match scores (0-100%) with detailed explanations of skills alignment, experience match, and gaps identified

### 4. Comprehensive Audit Trail
- **Notes for ALL Candidates**: Creates Bullhorn notes on both qualified (80%+) and non-qualified candidates
- **Qualified Notes**: Show all positions matched, scores, and key qualifications
- **Not Recommended Notes**: Show top matches and specific gaps identified
- **Full Logging**: Complete processing history with candidate IDs for troubleshooting

### 5. Collaborative Recruiter Notifications
- **One Email Per Candidate**: Single consolidated notification rather than separate emails per position
- **Primary Recipient**: Recruiter of the job the candidate applied to receives the email as "To:"
- **CC All Recruiters**: Other recruiters whose positions also matched are CC'd on the same thread
- **Admin BCC**: Administrator (kroots@myticas.com) is BCC'd on all notifications for transparency
- **Job Ownership Tags**: Each position in the email shows which recruiter owns it

---

## Technical Architecture

### Database Tables

| Table | Purpose |
|-------|---------|
| `VettingConfig` | System configuration (enabled/disabled, threshold, batch size) |
| `CandidateVettingLog` | Tracks each candidate's vetting status and results |
| `CandidateJobMatch` | Individual match scores for each candidate-job pair |
| `JobVettingRequirements` | Custom and AI-interpreted requirements per job |
| `ParsedEmail.vetted_at` | Tracks when an application has been vetted |

### Processing Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    5-MINUTE VETTING CYCLE                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. ACQUIRE LOCK (prevents overlapping cycles)                      │
│     └─> Skip if another cycle running                               │
│                                                                     │
│  2. DETECT UNVETTED APPLICATIONS                                    │
│     └─> Query ParsedEmail where vetted_at IS NULL                   │
│     └─> Fallback: Search Bullhorn for "Online Applicant" status     │
│                                                                     │
│  3. FOR EACH CANDIDATE (up to batch_size):                          │
│     a. Download resume from Bullhorn                                │
│     b. Extract text content                                         │
│     c. Get all active jobs from monitored tearsheets                │
│     d. For each job:                                                │
│        - Call GPT-4o to analyze match                               │
│        - Store score and explanations                               │
│     e. Create Bullhorn note (qualified or not)                      │
│     f. If qualified (80%+):                                         │
│        - Send recruiter notification email                          │
│     g. Mark ParsedEmail.vetted_at = now                             │
│                                                                     │
│  4. UPDATE LAST RUN TIMESTAMP                                       │
│                                                                     │
│  5. RELEASE LOCK                                                    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### AI Prompt Strategy

The GPT-4o prompt is carefully engineered to:

1. **Focus on Mandatory Requirements Only**
   - Identifies "required", "must have", "essential" qualifications
   - Ignores "preferred", "nice-to-have" qualifications
   - Lenient on soft skills, focuses on technical/hard requirements

2. **Structured JSON Response**
   ```json
   {
     "match_score": 85,
     "match_summary": "Strong match with 7 years of relevant experience...",
     "skills_match": "Python, AWS, Docker, Kubernetes...",
     "experience_match": "Senior DevOps Engineer for 5 years...",
     "gaps_identified": "No explicit CI/CD certification mentioned",
     "key_requirements": "• 5+ years DevOps\n• AWS certification\n• Python proficiency"
   }
   ```

3. **Custom Requirements Override**
   - If admin specifies custom requirements, AI uses those exclusively
   - Prevents AI misinterpretation of ambiguous job descriptions

---

## Configuration Settings

### Admin UI (`/vetting`)

| Setting | Description | Default |
|---------|-------------|---------|
| **Enable AI Vetting** | Master toggle for the entire system | OFF (production: ON) |
| **Match Threshold** | Minimum score for "qualified" status | 80% |
| **Batch Size** | Candidates processed per 5-minute cycle | 25 |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | API key for GPT-4o access |
| Bullhorn Credentials | Yes | Client ID, Secret, Username, Password |

---

## Bullhorn Note Examples

### Qualified Candidate Note
```
🎯 AI VETTING SUMMARY - QUALIFIED CANDIDATE

Analysis Date: 2026-01-30 15:30 UTC
Threshold: 80%
Qualified Matches: 2 of 5 jobs
Highest Match Score: 92%

QUALIFIED POSITIONS:

• Job ID: 12345 - Senior DevOps Engineer
  Match Score: 92%
  ⭐ APPLIED TO THIS POSITION
  Summary: Strong match with extensive AWS and Kubernetes experience
  Skills: Python, AWS, Docker, Kubernetes, Terraform

• Job ID: 12350 - Cloud Infrastructure Engineer
  Match Score: 85%
  Summary: Good alignment with cloud infrastructure requirements
  Skills: AWS, Azure, Infrastructure as Code
```

### Not Recommended Candidate Note
```
📋 AI VETTING SUMMARY - NOT RECOMMENDED

Analysis Date: 2026-01-30 15:30 UTC
Threshold: 80%
Highest Match Score: 45%
Jobs Analyzed: 5

This candidate did not meet the 80% match threshold for any current open positions.

TOP ANALYSIS RESULTS:

• Job ID: 12345 - Senior DevOps Engineer
  Match Score: 45%
  ⭐ APPLIED TO THIS POSITION
  Gaps: Missing required 5+ years DevOps experience (has 2 years)

• Job ID: 12350 - Cloud Infrastructure Engineer
  Match Score: 38%
  Gaps: No AWS certification, limited Kubernetes experience
```

---

## Email Notification Format

### Subject
`🎯 Qualified Candidate Alert: [Candidate Name]`

### Content Structure
1. **Transparency Header** (if multiple recruiters)
   - Shows who is CC'd on the email
   - Enables direct collaboration

2. **Candidate Card**
   - Name and direct link to Bullhorn profile
   - Click to view full candidate record

3. **Matched Positions** (for each job)
   - Job title with link to job order
   - APPLIED badge if candidate applied to this position
   - YOUR JOB / [Recruiter Name]'s Job badge
   - Match score percentage
   - AI-generated match summary
   - Key skills alignment

4. **Recommended Action**
   - Guidance for recruiter next steps

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/vetting` | GET | Settings dashboard |
| `/vetting/save` | POST | Save configuration settings |
| `/vetting/run` | POST | Manually trigger a vetting cycle |
| `/vetting/test-email` | POST | Send test notification email |
| `/vetting/sample-notes` | GET | Preview note formats |
| `/vetting/job/<job_id>/requirements` | POST | Set custom job requirements |

---

## Monitoring & Troubleshooting

### Activity Dashboard
The vetting settings page includes an activity dashboard showing:
- All Candidates: Complete list of vetted candidates
- Recommended (80%+): Qualified candidates only
- Not Recommended: Candidates below threshold

Each entry includes:
- Candidate name with Bullhorn link
- Match scores
- Expandable details
- Direct links to job orders

### Log Patterns to Watch
```
🚀 Starting candidate vetting cycle
🔍 Processing candidate: [Name] (ID: [ID])
✅ Completed analysis for [Name]: [X] qualified matches out of [Y] jobs
📧 Sent notification to [email] for [Name] (Candidate ID: [ID])
```

### Common Issues

| Issue | Solution |
|-------|----------|
| "AI analysis unavailable" | Check OPENAI_API_KEY is set |
| No candidates detected | Verify ParsedEmail records exist with status='completed' |
| Notes not created | Check Bullhorn credentials and commentingPerson ID |
| Emails not sending | Verify SENDGRID_API_KEY and recruiter email addresses |

---

## Performance Considerations

### Resource Usage
- **AI API Calls**: One GPT-4o call per candidate-job pair
- **Bullhorn API Calls**: Resume download, job fetch, note creation
- **Database**: Writes for logging and match storage

### Scaling Guidelines
| Batch Size | Cycle Time | Daily Capacity |
|------------|------------|----------------|
| 25 | ~3-5 min | ~7,200 candidates |
| 50 | ~6-10 min | ~14,400 candidates |
| 100 | ~15-20 min | ~28,800 candidates |

### Cost Estimation (GPT-4o)
- Average resume: ~2,000 tokens
- Average job description: ~1,000 tokens
- Cost per candidate-job match: ~$0.01-0.02
- 25 candidates × 5 jobs = ~$1.25-2.50 per cycle

---

## Security & Privacy

### Data Handling
- Resume text is extracted on-demand, not stored permanently
- Match scores and summaries stored for audit purposes
- No PII exposed in logs (only candidate IDs and names)

### Access Control
- Admin authentication required for settings
- API endpoints protected by Flask-Login
- Bullhorn credentials encrypted in database

### Email Security
- TLS encryption for all SendGrid communications
- BCC admin copy for audit trail
- No sensitive candidate data in email body

---

## Calibration Strategy

### First Few Weeks
1. Start with default threshold (80%) and batch size (25)
2. Monitor AI recommendations in the Audit Dashboard
3. Review Bullhorn notes for accuracy
4. Collect recruiter feedback on match quality

### Adjustments
- **Lower threshold** if good candidates are being missed
- **Raise threshold** if too many false positives
- **Add custom requirements** for jobs where AI interpretation is off
- **Increase batch size** if processing falls behind

---

## Version History

| Date | Version | Changes |
|------|---------|---------|
| 2026-01-30 | 1.0 | Initial release with ParsedEmail detection, GPT-4o matching, CC-based notifications |
| 2026-01-28 | 0.9 | Fixed note creation with commentingPerson ID |
| 2026-01-26 | 0.8 | Added BCC admin for transparency |
| 2026-01-20 | 0.7 | Implemented configurable batch size |
| 2026-01-15 | 0.5 | Beta release with basic matching |

---

## Contact & Support

For questions or issues with the AI Candidate Vetting Module:
- **Technical Support**: Contact your JobPulse administrator
- **System Administrator**: kroots@myticas.com
- **Documentation**: This document and `/vetting/sample-notes` for note previews
