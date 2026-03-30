# Bulk Candidate Deduplication — External Script Guide

## Overview

This document provides everything needed to build and run a standalone Python script that scans all Bullhorn candidates for duplicates, merges matching records, and logs results to the Scout Genius PostgreSQL database. This script is designed to run externally (e.g., Google Colab, local IDE) rather than inside the Scout Genius web application, avoiding web server timeouts and deployment cycles.

---

## What Already Exists (In Production)

### Scheduled Hourly Check (Stays in App)
- Runs every 60 minutes inside Scout Genius via APScheduler
- Scans candidates added/modified in the last 2 hours
- Lightweight — only checks recent activity
- **Do not remove this** — it handles ongoing dedup

### One-Time Bulk Scan (Move to External)
- Scans the entire Bullhorn candidate database (~216K+ records)
- Takes 30-40+ hours to complete
- Currently lives in the app as a one-time automation but suffers from:
  - Bullhorn REST token expiry (~10 min lifetime)
  - PostgreSQL SSL connection drops (Neon serverless)
  - Web server resource contention

---

## Bullhorn Authentication

### API Mode: Bullhorn One (Fixed Endpoints)

| Endpoint | URL |
|----------|-----|
| Auth URL | `https://auth-east.bullhornstaffing.com/oauth/authorize` |
| Token URL | `https://auth-east.bullhornstaffing.com/oauth/token` |
| REST Login | `https://rest-east.bullhornstaffing.com/rest-services/login` |
| REST Base URL | `https://rest45.bullhornstaffing.com/rest-services/dcc900/` |

### Credentials (Stored in Scout Genius DB — `global_settings` table)

| Setting Key | Description |
|-------------|-------------|
| `bullhorn_client_id` | OAuth client ID |
| `bullhorn_client_secret` | OAuth client secret |
| `bullhorn_username` | API username |
| `bullhorn_password` | API password (also available as env var `BULLHORN_PASSWORD`) |

### OAuth 2.0 Flow (3-Step)

```
Step 1: GET auth-east.bullhornstaffing.com/oauth/authorize
  Params: client_id, response_type=code, action=Login,
          username, password,
          redirect_uri=https://app.scoutgenius.ai/bullhorn/oauth/callback
  → Returns 302 redirect with ?code=XXXX

Step 2: POST auth-east.bullhornstaffing.com/oauth/token
  Params: grant_type=authorization_code, code=XXXX,
          client_id, client_secret,
          redirect_uri=https://app.scoutgenius.ai/bullhorn/oauth/callback
  → Returns { access_token, refresh_token }

Step 3: GET rest-east.bullhornstaffing.com/rest-services/login
  Params: version=2.0, access_token=XXXX
  → Returns { BhRestToken, restUrl }
```

### Critical Auth Behavior
- **BhRestToken lifetime**: ~10 minutes under load (can vary)
- **Must clear old token before re-auth**: The `authenticate()` method short-circuits if `rest_token` is already set. Always set `rest_token = None` and `base_url = None` before calling authenticate for a forced refresh.
- **5-second cooldown**: Bullhorn rejects auth attempts within 5 seconds of each other
- **Proactive refresh recommended**: Every 5 minutes (300 seconds), force a fresh token

### Corporate User ID
- `1147490` — Used as `commentingPerson` for note creation
- Can be retrieved via `GET {base_url}settings/userId?BhRestToken=XXX`

---

## Matching Logic

### Confidence Thresholds
- **≥ 0.85 (85%)** — Auto-merge threshold
- **1.00** — Primary email exact match
- **0.95** — Secondary email match (email2/email3)
- **0.90** — Phone + name match (both required)
- **0.00** — Phone match without name match (rejected)

### Email Matching
```python
emails_a = {email, email2, email3}  # all lowercased, stripped
emails_b = {email, email2, email3}
shared = emails_a & emails_b

if primary_email in other_set: confidence = 1.0, field = 'email'
elif any shared: confidence = 0.95, field = 'email_secondary'
```

### Phone + Name Matching
```python
# Phone: strip to digits, require ≥ 10 digits
phones_a = {digits(phone), digits(mobile)} where len ≥ 10
phones_b = {digits(phone), digits(mobile)} where len ≥ 10

if phones_a & phones_b:
    if names_match(a, b): confidence = 0.90, field = 'phone+name'
    else: confidence = 0.0 (rejected)
```

### Name Matching Rules
```python
def names_match(a, b):
    # Normalize: lowercase, remove all non-alpha chars
    first_a, last_a = normalize(a.firstName), normalize(a.lastName)
    first_b, last_b = normalize(b.firstName), normalize(b.lastName)

    # Require both first and last names to be non-empty
    if not all([first_a, first_b, last_a, last_b]): return False

    # Exact match
    if first_a == first_b and last_a == last_b: return True

    # First name exact + last name prefix (3 chars)
    if first_a == first_b and (last_a[:3] == last_b[:3]): return True

    # Last name exact + first name prefix (3 chars)
    if last_a == last_b and (first_a[:3] == first_b[:3]): return True

    return False
```

---

## Primary Record Selection

When two candidates match, determine which is the "primary" (keeper):

1. **Active placement wins**: If only one has an active placement (`status='Approved'`), it's the primary
2. **Both have placements**: Skip merge entirely — log as `skipped_both_placements`
3. **Neither has placement**: Most recently added (`dateAdded`) becomes primary
4. **Tie**: First candidate becomes primary (default)

Query for active placements:
```
GET {base_url}query/Placement
  ?where=candidate.id={id} AND status='Approved'
  &fields=id,status
  &count=1
  &BhRestToken=XXX
```

---

## Merge Process (Per Duplicate Pair)

### 1. Transfer Submissions
```
For each JobSubmission on duplicate:
  - Check if primary already has a submission for the same jobOrder.id
  - If not, create new submission on primary:
    PUT {base_url}entity/JobSubmission
    {
      "candidate": {"id": primary_id},
      "jobOrder": {"id": job_id},
      "status": original_status (default "New Lead"),
      "dateWebResponse": original_dateWebResponse,
      "source": original_source (default "Merged from duplicate"),
      "dateAdded": original_dateAdded  ← CRITICAL for timestamp preservation
    }
  - After creation, restore dateAdded:
    POST {base_url}entity/JobSubmission/{new_id}
    {"dateAdded": original_dateAdded}
    (Bullhorn ignores dateAdded on PUT, so POST update is required)
```

### 2. Transfer Notes
```
For each Note on duplicate:
  PUT {base_url}entity/Note
  {
    "personReference": {"id": primary_id},    ← MUST be Person entity
    "candidates": [{"id": primary_id}],
    "action": original_action (default "General Notes"),
    "comments": original_comments,
    "commentingPerson": {"id": 1147490},
    "isDeleted": false,
    "dateAdded": original_dateAdded
  }
  - After creation, restore dateAdded:
    POST {base_url}entity/Note/{new_id}
    {"dateAdded": original_dateAdded}
```

### 3. Transfer Files
```
For each file on duplicate:
  - Download: GET {base_url}file/Candidate/{dup_id}/{file_id}
    → Returns {"File": {"fileContent": base64_data, "name": "..."}}
  - Upload to primary: PUT {base_url}file/Candidate/{primary_id}
    {
      "externalID": "merged_{dup_id}_{file_id}",
      "fileContent": base64_content,
      "fileType": original_type (default "Resume"),
      "name": original_name,
      "description": "Merged from candidate {dup_id}"
    }
```

### 4. Enrich Primary
Fill in empty fields on primary from duplicate's data:
- Fields: `phone`, `mobile`, `occupation`, `companyName`, `skillSet`, `employmentPreference`, `email2`, `email3`
- Address fields: `address1`, `city`, `state`, `zip`, `countryID`
- Only fills blanks — never overwrites existing data

### 5. Add Merge Notes
- **On primary**: `[Scout Genius Auto-Merge] This record received data merged from duplicate candidate ID {dup_id}.` + original timestamp details
- **On duplicate**: `[Scout Genius Auto-Merge] This record was identified as a duplicate of candidate ID {primary_id}. All data has been transferred and this record has been archived.`

### 6. Archive Duplicate
```
POST {base_url}entity/Candidate/{dup_id}
{"status": "Archive"}
```

### 7. Log to Database
Insert into `candidate_merge_log`:
```sql
INSERT INTO candidate_merge_log (
  primary_candidate_id, duplicate_candidate_id,
  primary_name, duplicate_name,
  confidence_score, match_field,
  merge_type, items_transferred,
  merged_at, merged_by, skipped, skip_reason
) VALUES (
  primary_id, dup_id,
  'First Last', 'First Last',
  0.90, 'phone+name',
  'bulk', '{"submissions": 1, "notes": 3, "files": 2}',
  NOW(), 'system', false, null
);
```

---

## Scanning Strategy

### Batch Retrieval
```
GET {base_url}search/Candidate
  ?query=isDeleted:0 AND -status:Archive
  &fields=id,firstName,lastName,email,email2,email3,phone,mobile,dateAdded,status
  &count=200
  &start={offset}
  &sort=id
  &BhRestToken=XXX
```

**Important**: Must use `/search/Candidate` (Lucene syntax), NOT `/query/Candidate` (returns 400).

### For Each Candidate, Find Matches
For each candidate in the batch, search for potential duplicates using email and phone:

```python
# Build Lucene query for matching
terms = []
if email: terms.append(f'email:"{email}"')
if email: terms.append(f'email2:"{email}"')
if email: terms.append(f'email3:"{email}"')
if phone_digits (≥10): terms.append(f'phone:"{phone}"')
if mobile_digits (≥10): terms.append(f'mobile:"{mobile}"')

query = f'isDeleted:0 AND -status:Archive AND ({" OR ".join(terms)})'
```

### Skip Logic
- Skip candidates already processed in this scan (tracked by `processed_ids` set)
- Skip candidates already merged in previous runs (query `candidate_merge_log` for `duplicate_candidate_id` where `skipped=False`)
- Skip self-matches

### Rate Limiting / Throttling
- **Between merges**: 1.0 second sleep
- **Between candidate checks**: 0.3 second sleep
- **Between transfers** (submissions/notes/files): 0.5 second sleep
- **After token refresh**: 3 second settling delay

---

## Database Schema

### Table: `candidate_merge_log`

| Column | Type | Description |
|--------|------|-------------|
| `id` | serial PK | Auto-increment |
| `primary_candidate_id` | integer | Bullhorn ID of keeper |
| `duplicate_candidate_id` | integer | Bullhorn ID of archived dup |
| `primary_name` | varchar(200) | Name of primary |
| `duplicate_name` | varchar(200) | Name of duplicate |
| `confidence_score` | float | Match confidence (0.0-1.0) |
| `match_field` | varchar(50) | 'email', 'email_secondary', 'phone+name' |
| `merge_type` | varchar(20) | 'bulk', 'scheduled' |
| `items_transferred` | text (JSON) | '{"submissions": N, "notes": N, "files": N}' |
| `merged_at` | datetime | UTC timestamp |
| `merged_by` | varchar(100) | 'system' |
| `skipped` | boolean | false=merged, true=skipped |
| `skip_reason` | varchar(500) | Reason if skipped |

### Indexes
- `idx_merge_log_primary` on `primary_candidate_id`
- `idx_merge_log_duplicate` on `duplicate_candidate_id`
- `idx_merge_log_merged_at` on `merged_at`

---

## Progress & Completion

### Progress Logging
Log every 200 candidates:
```
📊 Bulk scan progress: scanned=X, merged=Y, skipped_placement=Z
```

### Completion Email
When scan finishes, send notification to `kroots@myticas.com` via SendGrid with:
- Total candidates scanned
- Duplicates found and merged
- Skipped (placement conflicts)
- Errors

---

## What Has Already Been Done

### Run 1 (Pre-Fix)
- Scanned 3,200 candidates, merged 307, 0 errors
- Stopped due to Bullhorn 401 token expiry (no refresh logic)
- All 307 merges verified as legitimate

### Timestamp Correction
- A one-time correction tool was run to fix 190 submissions and 2,043 notes
  that had their `dateAdded` overwritten during Run 1 (before timestamp preservation was added)
- This tool has been removed from the codebase — no longer needed
- All future merges include timestamp preservation in the transfer methods

### Current State
- ~3,200 candidates have been scanned and processed
- The remaining ~213K+ candidates need to be scanned
- The scheduled hourly check continues to catch new duplicates as they arrive

---

## Environment Requirements

### Python Dependencies
```
requests
psycopg2-binary (or psycopg2)
```

### Environment Variables / Secrets Needed
- `DATABASE_URL` — PostgreSQL connection string (Neon-hosted)
- Bullhorn credentials (from `global_settings` table or provided directly):
  - `bullhorn_client_id`
  - `bullhorn_client_secret`
  - `bullhorn_username`
  - `bullhorn_password` (also env var `BULLHORN_PASSWORD`)

### Key Operational Notes
1. **Token refresh every 5 minutes** — Clear `rest_token` and `base_url` before re-authenticating
2. **DB connection keepalives** — Use `keepalives=1, keepalives_idle=30` in psycopg2 connect args
3. **Handle DB disconnects** — Wrap each merge's DB commit in try/except with reconnect logic
4. **Batch size**: 200 candidates per Bullhorn search request
5. **Console output**: Print progress to stdout for real-time visibility
6. **Resumability**: Can track `start` offset to resume from where it left off if interrupted
