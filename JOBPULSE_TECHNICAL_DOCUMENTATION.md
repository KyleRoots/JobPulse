# JobPulse™ Technical Documentation
## Complete System Reference Guide

**Document Version:** 1.0  
**Last Updated:** February 1, 2026  
**Application Version:** Production  
**Platform:** Replit Reserved VM (2 vCPU / 8 GiB RAM)

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture & Technology Stack](#2-architecture--technology-stack)
3. [Database Schema](#3-database-schema)
4. [Bullhorn ATS Integration](#4-bullhorn-ats-integration)
5. [XML Job Feed Processing](#5-xml-job-feed-processing)
6. [Tearsheet Monitoring System](#6-tearsheet-monitoring-system)
7. [Scheduled Jobs & Background Processing](#7-scheduled-jobs--background-processing)
8. [Email Services](#8-email-services)
9. [Job Application Forms](#9-job-application-forms)
10. [AI Candidate Vetting Module](#10-ai-candidate-vetting-module)
11. [Job Classification System](#11-job-classification-system)
12. [Resume Parsing](#12-resume-parsing)
13. [Settings & Configuration](#13-settings--configuration)
14. [API Endpoints Reference](#14-api-endpoints-reference)
15. [User Interface Guide](#15-user-interface-guide)
16. [Security & Authentication](#16-security--authentication)
17. [Health Monitoring](#17-health-monitoring)
18. [Troubleshooting Guide](#18-troubleshooting-guide)
19. [File Structure Reference](#19-file-structure-reference)

---

## 1. System Overview

### What is JobPulse?

JobPulse™ is a comprehensive job feed automation system designed for staffing and recruiting companies that use Bullhorn ATS/CRM. It automates the complex process of:

1. **Maintaining Accurate Job Listings** - Automatically syncs jobs from Bullhorn tearsheets to XML feeds for job boards
2. **Reference Number Management** - Generates and preserves unique reference numbers for job tracking
3. **Real-Time Monitoring** - Watches for job changes every 5 minutes
4. **AI-Powered Candidate Vetting** - Analyzes incoming applicants against all open positions
5. **Automated Notifications** - Sends email alerts for new jobs, qualified candidates, and system events

### Core Value Proposition

| Problem | JobPulse Solution |
|---------|-------------------|
| Manual XML feed updates | Automatic 5-minute sync cycles |
| Inconsistent reference numbers | Database-first preservation system |
| Missed candidate matches | AI vetting scores all candidates against all jobs |
| Delayed recruiter notifications | Real-time email alerts with collaborative threading |
| Data entry errors | Direct Bullhorn API integration |

### Dual-Domain Architecture

JobPulse operates across two domains:

| Domain | Purpose |
|--------|---------|
| `jobpulse.lyntrix.ai` | Main application (admin dashboard, monitoring, vetting) |
| `apply.myticas.com` | Public job application forms (Myticas branding) |
| `apply.stsigroup.com` | Public job application forms (STSI branding) |

---

## 2. Architecture & Technology Stack

### System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            JOBPULSE SYSTEM                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐         │
│  │   FLASK WEB     │    │   BACKGROUND    │    │   DATABASE      │         │
│  │   APPLICATION   │    │   SCHEDULER     │    │   (PostgreSQL)  │         │
│  │                 │    │   (APScheduler) │    │                 │         │
│  │  • Routes       │    │                 │    │  • 21 Tables    │         │
│  │  • Templates    │    │  • 5-min cycle  │    │  • Neon-backed  │         │
│  │  • API endpoints│    │  • 30-min SFTP  │    │  • SQLAlchemy   │         │
│  └────────┬────────┘    └────────┬────────┘    └────────┬────────┘         │
│           │                      │                      │                   │
│           └──────────────────────┼──────────────────────┘                   │
│                                  │                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                       SERVICE LAYER                                  │   │
│  ├─────────────────────────────────────────────────────────────────────┤   │
│  │  BullhornService        │  EmailService          │  ResumeParser    │   │
│  │  • OAuth authentication │  • SendGrid API        │  • PDF extraction│   │
│  │  • REST API calls       │  • Delivery logging    │  • DOCX parsing  │   │
│  │  • Tearsheet access     │  • Duplicate prevention│  • AI formatting │   │
│  ├─────────────────────────────────────────────────────────────────────┤   │
│  │  IncrementalMonitoring  │  CandidateVettingService│ XMLIntegration  │   │
│  │  • Job sync cycles      │  • AI resume matching   │  • lxml parsing │   │
│  │  • Change detection     │  • Note creation        │  • Job building │   │
│  │  • Auto-cleanup         │  • Recruiter alerts     │  • SFTP upload  │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      EXTERNAL INTEGRATIONS                           │   │
│  ├─────────────────────────────────────────────────────────────────────┤   │
│  │  BULLHORN ATS    │  OPENAI GPT-4o   │  SENDGRID    │  SFTP SERVER   │   │
│  │  (OAuth 2.0)     │  (API Key)       │  (API Key)   │  (SSH)         │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Technology Stack

| Component | Technology | Version/Details |
|-----------|------------|-----------------|
| **Web Framework** | Flask | Python 3.11 |
| **Database** | PostgreSQL | Neon-hosted |
| **ORM** | SQLAlchemy | Flask-SQLAlchemy |
| **Template Engine** | Jinja2 | With Bootstrap 5 dark theme |
| **Authentication** | Flask-Login | Session-based |
| **Background Jobs** | APScheduler | BackgroundScheduler |
| **XML Processing** | lxml | etree parser |
| **PDF Parsing** | PyMuPDF (fitz) | Primary; PyPDF2 fallback |
| **DOCX Parsing** | python-docx | For Word documents |
| **AI/LLM** | OpenAI GPT-4o | Resume analysis, job matching |
| **Email** | SendGrid | Transactional emails |
| **File Transfer** | Paramiko | SFTP uploads |
| **HTTP Client** | Requests | API calls |

### Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `SESSION_SECRET` | Flask session encryption key | Yes |
| `SENDGRID_API_KEY` | Email service authentication | Yes |
| `OPENAI_API_KEY` | AI features (vetting, classification) | Yes |
| `BULLHORN_PASSWORD` | Bullhorn OAuth password | Yes |
| `OAUTH_REDIRECT_BASE_URL` | OAuth callback URL base | Yes |

---

## 3. Database Schema

### Complete Table Inventory (21 Tables)

#### Core Data Tables

```
┌─────────────────────────────────────────────────────────────────────┐
│                         job_reference_number                        │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  bullhorn_job_id     VARCHAR(50) UNIQUE NOT NULL  -- Bullhorn ID    │
│  reference_number    VARCHAR(50) NOT NULL         -- Preserved ref  │
│  job_title           VARCHAR(500)                 -- For display    │
│  created_at          TIMESTAMP                                      │
│  updated_at          TIMESTAMP                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Single source of truth for job reference numbers          │
│  Updated: During monitoring cycles and manual refresh               │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                         bullhorn_monitor                            │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  name                VARCHAR(100) NOT NULL        -- Monitor name   │
│  tearsheet_id        INTEGER NOT NULL             -- Bullhorn ID    │
│  tearsheet_name      VARCHAR(255)                 -- Display name   │
│  is_active           BOOLEAN DEFAULT TRUE                           │
│  check_interval_min  INTEGER DEFAULT 5                              │
│  last_check          TIMESTAMP                                      │
│  next_check          TIMESTAMP NOT NULL                             │
│  notification_email  VARCHAR(255)                                   │
│  send_notifications  BOOLEAN DEFAULT TRUE                           │
│  last_job_snapshot   TEXT                         -- JSON job list  │
│  created_at          TIMESTAMP                                      │
│  updated_at          TIMESTAMP                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Configuration for each monitored tearsheet                │
│  Current Monitors: 6 (OTT, CHI, CLE, VMS, GR, STSI)                │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                         bullhorn_activity                           │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  monitor_id          INTEGER FK -> bullhorn_monitor                 │
│  activity_type       VARCHAR(20)  -- job_added, job_removed, etc.  │
│  job_id              INTEGER                      -- Bullhorn job   │
│  job_title           VARCHAR(255)                                   │
│  account_manager     VARCHAR(255)                 -- Job owner      │
│  details             TEXT                         -- JSON details   │
│  notification_sent   BOOLEAN DEFAULT FALSE                          │
│  created_at          TIMESTAMP                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Audit log of all job changes detected                     │
│  Activity Types: job_added, job_removed, job_modified, check_done   │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                      tearsheet_job_history                          │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  tearsheet_id        INTEGER NOT NULL                               │
│  job_id              INTEGER NOT NULL                               │
│  job_title           VARCHAR(500)                                   │
│  job_description     TEXT                                           │
│  job_city            VARCHAR(255)                                   │
│  job_state           VARCHAR(255)                                   │
│  job_country         VARCHAR(255)                                   │
│  job_owner_name      VARCHAR(255)                                   │
│  job_remote_type     VARCHAR(100)                                   │
│  is_current          BOOLEAN DEFAULT TRUE                           │
│  timestamp           TIMESTAMP                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Historical snapshots for detecting job modifications      │
│  Indexed: (tearsheet_id, job_id, is_current)                       │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                       recruiter_mapping                             │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  recruiter_name      VARCHAR(255) UNIQUE NOT NULL                   │
│  linkedin_tag        VARCHAR(20) NOT NULL         -- e.g., #LI-RP1 │
│  created_at          TIMESTAMP                                      │
│  updated_at          TIMESTAMP                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Map recruiter names to LinkedIn tags for job posts        │
│  Current Records: 38 recruiters mapped                              │
└─────────────────────────────────────────────────────────────────────┘
```

#### AI Vetting Tables

```
┌─────────────────────────────────────────────────────────────────────┐
│                      candidate_vetting_log                          │
├─────────────────────────────────────────────────────────────────────┤
│  id                      INTEGER PRIMARY KEY                        │
│  bullhorn_candidate_id   INTEGER UNIQUE NOT NULL                    │
│  candidate_name          VARCHAR(255)                               │
│  candidate_email         VARCHAR(255)                               │
│  applied_job_id          INTEGER              -- Original job       │
│  applied_job_title       VARCHAR(500)                               │
│  resume_text             TEXT                 -- Extracted content  │
│  resume_file_id          INTEGER              -- Bullhorn file ID   │
│  status                  VARCHAR(50)          -- pending/completed  │
│  is_qualified            BOOLEAN DEFAULT FALSE                      │
│  highest_match_score     FLOAT DEFAULT 0.0                          │
│  total_jobs_matched      INTEGER DEFAULT 0                          │
│  note_created            BOOLEAN DEFAULT FALSE                      │
│  bullhorn_note_id        INTEGER                                    │
│  notifications_sent      BOOLEAN DEFAULT FALSE                      │
│  notification_count      INTEGER DEFAULT 0                          │
│  error_message           TEXT                                       │
│  detected_at             TIMESTAMP                                  │
│  analyzed_at             TIMESTAMP                                  │
│  created_at              TIMESTAMP                                  │
│  updated_at              TIMESTAMP                                  │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Track candidates through AI vetting pipeline              │
│  Relationship: Has many CandidateJobMatch records                   │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                       candidate_job_match                           │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  vetting_log_id      INTEGER FK -> candidate_vetting_log            │
│  bullhorn_job_id     INTEGER NOT NULL                               │
│  job_title           VARCHAR(500)                                   │
│  job_location        VARCHAR(255)                                   │
│  tearsheet_id        INTEGER                                        │
│  tearsheet_name      VARCHAR(255)                                   │
│  recruiter_name      VARCHAR(255)                                   │
│  recruiter_email     VARCHAR(255)                                   │
│  recruiter_bullhorn_id INTEGER                                      │
│  match_score         FLOAT NOT NULL DEFAULT 0.0  -- 0-100%         │
│  is_qualified        BOOLEAN DEFAULT FALSE       -- score >= 80%   │
│  is_applied_job      BOOLEAN DEFAULT FALSE       -- original job   │
│  match_summary       TEXT                        -- AI explanation │
│  skills_match        TEXT                        -- Skills detail  │
│  experience_match    TEXT                        -- Exp detail     │
│  gaps_identified     TEXT                        -- Missing items  │
│  notification_sent   BOOLEAN DEFAULT FALSE                          │
│  notification_sent_at TIMESTAMP                                     │
│  created_at          TIMESTAMP                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Store individual job match scores for each candidate      │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                    job_vetting_requirements                         │
├─────────────────────────────────────────────────────────────────────┤
│  id                      INTEGER PRIMARY KEY                        │
│  bullhorn_job_id         INTEGER UNIQUE NOT NULL                    │
│  job_title               VARCHAR(255)                               │
│  job_location            VARCHAR(255)                               │
│  job_work_type           VARCHAR(50)          -- On-site/Hybrid/Remote│
│  custom_requirements     TEXT                 -- User override      │
│  ai_interpreted_requirements TEXT             -- AI extracted       │
│  last_ai_interpretation  TIMESTAMP                                  │
│  created_at              TIMESTAMP                                  │
│  updated_at              TIMESTAMP                                  │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Store job requirements for vetting (editable by user)     │
│  Logic: Uses custom_requirements if set, else ai_interpreted        │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                         vetting_config                              │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  setting_key         VARCHAR(100) UNIQUE NOT NULL                   │
│  setting_value       TEXT                                           │
│  description         VARCHAR(500)                                   │
│  created_at          TIMESTAMP                                      │
│  updated_at          TIMESTAMP                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Keys: vetting_enabled, match_threshold, notification_email,        │
│        batch_size, health_alert_email, check_interval_minutes       │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                      vetting_health_check                           │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  check_time          TIMESTAMP NOT NULL                             │
│  bullhorn_status     BOOLEAN DEFAULT TRUE                           │
│  openai_status       BOOLEAN DEFAULT TRUE                           │
│  database_status     BOOLEAN DEFAULT TRUE                           │
│  scheduler_status    BOOLEAN DEFAULT TRUE                           │
│  bullhorn_error      TEXT                                           │
│  openai_error        TEXT                                           │
│  database_error      TEXT                                           │
│  scheduler_error     TEXT                                           │
│  is_healthy          BOOLEAN DEFAULT TRUE                           │
│  candidates_processed_today INTEGER DEFAULT 0                       │
│  candidates_pending  INTEGER DEFAULT 0                              │
│  emails_sent_today   INTEGER DEFAULT 0                              │
│  last_successful_cycle TIMESTAMP                                    │
│  alert_sent          BOOLEAN DEFAULT FALSE                          │
│  alert_sent_at       TIMESTAMP                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Track vetting system health for monitoring dashboard      │
└─────────────────────────────────────────────────────────────────────┘
```

#### Email & Inbound Processing Tables

```
┌─────────────────────────────────────────────────────────────────────┐
│                        parsed_email                                 │
├─────────────────────────────────────────────────────────────────────┤
│  id                      INTEGER PRIMARY KEY                        │
│  message_id              VARCHAR(255) UNIQUE                        │
│  sender_email            VARCHAR(255) NOT NULL                      │
│  recipient_email         VARCHAR(255) NOT NULL                      │
│  subject                 VARCHAR(500)                               │
│  source_platform         VARCHAR(50)     -- Dice, LinkedIn, etc.   │
│  bullhorn_job_id         INTEGER                                    │
│  candidate_name          VARCHAR(255)                               │
│  candidate_email         VARCHAR(255)                               │
│  candidate_phone         VARCHAR(50)                                │
│  status                  VARCHAR(50) DEFAULT 'received'             │
│  processing_notes        TEXT                                       │
│  bullhorn_candidate_id   INTEGER                                    │
│  bullhorn_submission_id  INTEGER                                    │
│  is_duplicate_candidate  BOOLEAN DEFAULT FALSE                      │
│  duplicate_confidence    FLOAT                                      │
│  resume_filename         VARCHAR(255)                               │
│  resume_file_id          INTEGER                                    │
│  received_at             TIMESTAMP                                  │
│  processed_at            TIMESTAMP                                  │
│  vetted_at               TIMESTAMP                                  │
│  created_at              TIMESTAMP                                  │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Track inbound job application emails from job boards      │
│  Source Detection: Dice, LinkedIn, Indeed, ZipRecruiter            │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                      email_delivery_log                             │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  notification_type   VARCHAR(50) NOT NULL                           │
│  job_id              VARCHAR(20)                                    │
│  job_title           VARCHAR(255)                                   │
│  recipient_email     VARCHAR(255) NOT NULL                          │
│  sent_at             TIMESTAMP NOT NULL                             │
│  delivery_status     VARCHAR(20) DEFAULT 'sent'                     │
│  sendgrid_message_id VARCHAR(255)                                   │
│  error_message       TEXT                                           │
│  schedule_name       VARCHAR(100)                                   │
│  changes_summary     TEXT                                           │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Audit log for all email notifications sent                │
│  Used for: Duplicate prevention (5-minute threshold)               │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                     email_parsing_config                            │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  setting_key         VARCHAR(100) UNIQUE NOT NULL                   │
│  setting_value       TEXT                                           │
│  created_at          TIMESTAMP                                      │
│  updated_at          TIMESTAMP                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Configuration for inbound email parsing                   │
└─────────────────────────────────────────────────────────────────────┘
```

#### Configuration & Operations Tables

```
┌─────────────────────────────────────────────────────────────────────┐
│                        global_settings                              │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  setting_key         VARCHAR(100) UNIQUE NOT NULL                   │
│  setting_value       TEXT                                           │
│  created_at          TIMESTAMP                                      │
│  updated_at          TIMESTAMP                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Keys: bullhorn_client_id, bullhorn_client_secret,                  │
│        bullhorn_username, bullhorn_password, sftp_hostname,         │
│        sftp_username, sftp_password, sftp_directory, sftp_port,     │
│        notification_email                                           │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                        schedule_config                              │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  name                VARCHAR(100) NOT NULL                          │
│  file_path           VARCHAR(500) NOT NULL                          │
│  original_filename   VARCHAR(255)                                   │
│  schedule_days       INTEGER DEFAULT 7                              │
│  last_run            TIMESTAMP                                      │
│  next_run            TIMESTAMP NOT NULL                             │
│  is_active           BOOLEAN DEFAULT TRUE                           │
│  notification_email  VARCHAR(255)                                   │
│  send_email_notifications BOOLEAN DEFAULT FALSE                     │
│  auto_upload_ftp     BOOLEAN DEFAULT FALSE                          │
│  created_at          TIMESTAMP                                      │
│  updated_at          TIMESTAMP                                      │
│  last_file_upload    TIMESTAMP                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Configuration for scheduled XML processing tasks          │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                        processing_log                               │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  schedule_config_id  INTEGER FK -> schedule_config                  │
│  file_path           VARCHAR(500) NOT NULL                          │
│  processing_type     VARCHAR(20) NOT NULL   -- scheduled/manual    │
│  jobs_processed      INTEGER DEFAULT 0                              │
│  success             BOOLEAN NOT NULL                               │
│  error_message       TEXT                                           │
│  processed_at        TIMESTAMP                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Audit log of all XML processing operations                │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                         refresh_log                                 │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  refresh_date        DATE NOT NULL                                  │
│  refresh_time        TIMESTAMP NOT NULL                             │
│  jobs_updated        INTEGER DEFAULT 0                              │
│  processing_time     FLOAT DEFAULT 0.0                              │
│  email_sent          BOOLEAN DEFAULT FALSE                          │
│  created_at          TIMESTAMP                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Track reference number refresh operations                 │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                        scheduler_lock                               │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY (always 1)                 │
│  owner_process_id    VARCHAR(100) NOT NULL                          │
│  acquired_at         TIMESTAMP NOT NULL                             │
│  expires_at          TIMESTAMP NOT NULL                             │
│  environment         VARCHAR(20) NOT NULL   -- production/dev      │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Ensure only one scheduler runs (production takes priority)│
│  TTL: 5 minutes, auto-renewed by active scheduler                  │
└─────────────────────────────────────────────────────────────────────┘
```

#### Authentication & Monitoring Tables

```
┌─────────────────────────────────────────────────────────────────────┐
│                            user                                     │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  username            VARCHAR(80) UNIQUE NOT NULL                    │
│  email               VARCHAR(120) UNIQUE NOT NULL                   │
│  password_hash       VARCHAR(256) NOT NULL                          │
│  is_admin            BOOLEAN DEFAULT FALSE                          │
│  created_at          TIMESTAMP                                      │
│  last_login          TIMESTAMP                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Admin user authentication                                 │
│  Current: Single admin user (kroots@myticas.com)                   │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                      environment_status                             │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  environment_name    VARCHAR(100) DEFAULT 'production'              │
│  environment_url     VARCHAR(500) NOT NULL                          │
│  current_status      VARCHAR(20) DEFAULT 'unknown'  -- up/down     │
│  last_check_time     TIMESTAMP NOT NULL                             │
│  last_status_change  TIMESTAMP                                      │
│  consecutive_failures INTEGER DEFAULT 0                             │
│  total_downtime_minutes FLOAT DEFAULT 0.0                           │
│  check_interval_minutes INTEGER DEFAULT 5                           │
│  timeout_seconds     INTEGER DEFAULT 30                             │
│  alert_email         VARCHAR(255) DEFAULT 'kroots@myticas.com'      │
│  alert_on_down       BOOLEAN DEFAULT TRUE                           │
│  alert_on_recovery   BOOLEAN DEFAULT TRUE                           │
│  created_at          TIMESTAMP                                      │
│  updated_at          TIMESTAMP                                      │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Monitor production environment uptime                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                      environment_alert                              │
├─────────────────────────────────────────────────────────────────────┤
│  id                  INTEGER PRIMARY KEY                            │
│  environment_status_id INTEGER FK -> environment_status             │
│  alert_type          VARCHAR(20) NOT NULL  -- down/up/degraded     │
│  alert_message       TEXT NOT NULL                                  │
│  recipient_email     VARCHAR(255) NOT NULL                          │
│  sent_at             TIMESTAMP NOT NULL                             │
│  delivery_status     VARCHAR(20) DEFAULT 'sent'                     │
│  sendgrid_message_id VARCHAR(255)                                   │
│  error_message       TEXT                                           │
│  downtime_duration   FLOAT                  -- minutes              │
│  error_details       TEXT                                           │
├─────────────────────────────────────────────────────────────────────┤
│  Purpose: Log of environment up/down alerts sent                    │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Bullhorn ATS Integration

### Authentication Flow

JobPulse uses Bullhorn's OAuth 2.0 authentication with the following flow:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    BULLHORN AUTHENTICATION FLOW                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. Authorization Request                                           │
│     ┌──────────────┐         ┌──────────────────────┐              │
│     │  JobPulse    │ ──────► │ Bullhorn Auth Server │              │
│     └──────────────┘         │ auth-east.bullhorn   │              │
│         Sends:               │ staffing.com/oauth/  │              │
│         - client_id          │ authorize            │              │
│         - redirect_uri       └──────────────────────┘              │
│         - username                      │                          │
│         - password                      ▼                          │
│                              ┌──────────────────────┐              │
│  2. Authorization Code       │  Returns auth_code   │              │
│     ◄─────────────────────── └──────────────────────┘              │
│                                                                     │
│  3. Token Exchange                                                  │
│     ┌──────────────┐         ┌──────────────────────┐              │
│     │  JobPulse    │ ──────► │ Bullhorn Token URL   │              │
│     └──────────────┘         │ /oauth/token         │              │
│         Sends:               └──────────────────────┘              │
│         - auth_code                     │                          │
│         - client_id                     ▼                          │
│         - client_secret      ┌──────────────────────┐              │
│                              │ Returns access_token │              │
│  4. Access Token             │ & refresh_token      │              │
│     ◄─────────────────────── └──────────────────────┘              │
│                                                                     │
│  5. REST Login                                                      │
│     ┌──────────────┐         ┌──────────────────────┐              │
│     │  JobPulse    │ ──────► │ Bullhorn REST Login  │              │
│     └──────────────┘         │ /rest-services/login │              │
│         Sends:               └──────────────────────┘              │
│         - access_token                  │                          │
│                                         ▼                          │
│  6. REST Token + Base URL    ┌──────────────────────┐              │
│     ◄─────────────────────── │ Returns BhRestToken  │              │
│                              │ & restUrl            │              │
│                              └──────────────────────┘              │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Bullhorn API Endpoints Used

| Endpoint | Purpose |
|----------|---------|
| `GET /entity/Tearsheet/{id}` | Get tearsheet details |
| `GET /entity/Tearsheet/{id}/candidates` | Get jobs in tearsheet |
| `GET /entity/JobOrder/{id}` | Get job details |
| `GET /search/Candidate` | Search candidates |
| `GET /entity/Candidate/{id}/fileAttachments` | Get resume files |
| `GET /file/Candidate/{id}/raw` | Download resume content |
| `PUT /entity/Note` | Create note on candidate |
| `PUT /entity/JobSubmission` | Create job submission |

### BullhornService Class

Located in `bullhorn_service.py` (~77KB, 1739 lines)

**Key Methods:**

| Method | Purpose |
|--------|---------|
| `authenticate()` | Full OAuth flow, returns True/False |
| `get_tearsheet_jobs(tearsheet_id)` | Get all jobs from a tearsheet |
| `get_job_details(job_id)` | Get full job information |
| `search_candidates(query)` | Search Bullhorn candidates |
| `get_candidate_resume(candidate_id)` | Download resume file |
| `create_candidate_note(candidate_id, note)` | Add note to candidate |
| `create_job_submission(candidate_id, job_id)` | Submit candidate to job |

### Excluded Job IDs

Certain jobs are explicitly excluded from the feed:

```python
self.excluded_job_ids = {31939, 34287}  # Set for O(1) lookup
```

---

## 5. XML Job Feed Processing

### XML File Structure

The generated XML file follows this structure:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<jobs>
    <job>
        <bhatsid>123456</bhatsid>          <!-- Bullhorn Job ID -->
        <title>Senior Software Engineer</title>
        <referencenumber>MYT-2024-001</referencenumber>
        <description><![CDATA[...HTML...]]></description>
        <city>Chicago</city>
        <state>IL</state>
        <country>US</country>
        <postalcode>60601</postalcode>
        <date>2024-01-15T10:30:00Z</date>
        <category>Information Technology</category>
        <industry>Technology, Information and Media</industry>
        <jobfunction>Engineering</jobfunction>
        <salary_min>120000</salary_min>
        <salary_max>150000</salary_max>
        <experience>Mid-Senior level</experience>
        <type>Direct Hire</type>
        <remotetype>Hybrid</remotetype>
        <apply_url>https://apply.myticas.com/123456/Senior-Software-Engineer</apply_url>
    </job>
    <!-- More jobs... -->
</jobs>
```

### Reference Number Generation

Reference numbers follow the format: `MYT-YYYY-###` where:
- `MYT` = Company prefix (Myticas)
- `YYYY` = Year
- `###` = Sequential number

**Database-First Architecture:**
- The `job_reference_number` table is the single source of truth
- New jobs get new sequential numbers
- Existing jobs preserve their assigned numbers
- File snapshots were removed; database is authoritative

### XML Processing Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                     XML PROCESSING PIPELINE                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. FETCH JOBS                                                      │
│     ┌─────────────────────────────────────────────────────────┐    │
│     │ For each tearsheet (6 total):                           │    │
│     │   - Call Bullhorn API                                   │    │
│     │   - Filter out excluded job IDs                         │    │
│     │   - Filter out ineligible statuses                      │    │
│     └─────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  2. CLASSIFY JOBS (AI)                                              │
│     ┌─────────────────────────────────────────────────────────┐    │
│     │ For each job:                                           │    │
│     │   - Send to GPT-4o for classification                   │    │
│     │   - Get: job_function, industry, seniority_level        │    │
│     │   - Map to LinkedIn taxonomy (28 functions, 20 industries)│  │
│     └─────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  3. ASSIGN REFERENCE NUMBERS                                        │
│     ┌─────────────────────────────────────────────────────────┐    │
│     │   - Check database for existing reference               │    │
│     │   - If exists: use existing number                      │    │
│     │   - If new: generate next sequential number             │    │
│     │   - Save to job_reference_number table                  │    │
│     └─────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  4. BUILD XML                                                       │
│     ┌─────────────────────────────────────────────────────────┐    │
│     │   - Create XML structure with lxml                      │    │
│     │   - Add recruiter LinkedIn tags (#LI-XX1)               │    │
│     │   - Wrap description in CDATA                           │    │
│     │   - Generate apply URLs                                  │    │
│     └─────────────────────────────────────────────────────────┘    │
│                              │                                      │
│                              ▼                                      │
│  5. SAVE & UPLOAD                                                   │
│     ┌─────────────────────────────────────────────────────────┐    │
│     │   - Save to myticas-job-feed-v2.xml                     │    │
│     │   - Upload via SFTP to job board server                 │    │
│     │   - Log upload status                                   │    │
│     └─────────────────────────────────────────────────────────┘    │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Zero-Job Safeguard

To prevent XML corruption from empty API responses:

```python
if not all_jobs_with_context:
    raise Exception("No jobs found in any tearsheets")
```

When zero jobs are detected:
1. XML update is blocked
2. Backup of current file is created
3. Alert email is sent to administrator

---

## 6. Tearsheet Monitoring System

### Monitored Tearsheets (6 Total)

| Monitor Name | Tearsheet ID | Purpose |
|--------------|--------------|---------|
| Sponsored - OTT | 1231 | Ottawa office jobs |
| Sponsored - CHI | 1232 | Chicago office jobs |
| Sponsored - CLE | 1233 | Cleveland office jobs |
| Sponsored - VMS | 1239 | VMS/MSP jobs |
| Sponsored - GR | 1474 | Grand Rapids jobs |
| Sponsored - STSI | 1531 | STSI division jobs |

### Monitoring Cycle (5-Minute Interval)

```
┌─────────────────────────────────────────────────────────────────────┐
│                   5-MINUTE MONITORING CYCLE                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  STEP 1: Acquire Lock (prevent concurrent runs)                     │
│          └─► Check scheduler_lock table                             │
│          └─► Skip if another process owns lock                      │
│                                                                     │
│  STEP 2: For Each Active Monitor                                    │
│          ├─► Authenticate with Bullhorn                             │
│          ├─► Fetch current job list from tearsheet                  │
│          └─► Compare with last_job_snapshot                         │
│                                                                     │
│  STEP 3: Detect Changes                                             │
│          ├─► NEW JOBS: Jobs in current but not in snapshot          │
│          ├─► REMOVED JOBS: Jobs in snapshot but not in current      │
│          └─► MODIFIED JOBS: Compare dateLastModified timestamps     │
│                                                                     │
│  STEP 4: Smart Auto-Cleanup                                         │
│          └─► For jobs with ineligible status, remove from tearsheet │
│              Ineligible statuses:                                   │
│              • Qualifying, Hold - Covered, Hold - Client Hold       │
│              • Offer Out, Filled, Lost - Competition                │
│              • Lost - Filled Internally, Lost - Funding             │
│              • Canceled, Placeholder/MPC, Archive                   │
│                                                                     │
│  STEP 5: Update Database                                            │
│          ├─► Create bullhorn_activity records                       │
│          ├─► Update last_job_snapshot                               │
│          └─► Update tearsheet_job_history                           │
│                                                                     │
│  STEP 6: Process AI Requirements                                    │
│          └─► For modified jobs, re-extract AI requirements          │
│              (Used by AI vetting system)                            │
│                                                                     │
│  STEP 7: Generate & Upload XML                                      │
│          ├─► Rebuild XML with current jobs                          │
│          └─► Upload to SFTP server (every 30 min)                   │
│                                                                     │
│  STEP 8: Release Lock                                               │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### IncrementalMonitoringService

Located in `incremental_monitoring_service.py` (~53KB, 1166 lines)

**Key Methods:**

| Method | Purpose |
|--------|---------|
| `run_monitoring_cycle()` | Main entry point for 5-min cycle |
| `_detect_changes(current, previous)` | Find added/removed/modified jobs |
| `_auto_cleanup_ineligible_jobs()` | Remove closed jobs from tearsheets |
| `_generate_and_upload_xml()` | Build and SFTP upload |
| `_acquire_lock()` / `_release_lock()` | Concurrency control |

---

## 7. Scheduled Jobs & Background Processing

### APScheduler Configuration

The application uses APScheduler's `BackgroundScheduler` with the following jobs:

```python
scheduler = BackgroundScheduler()

# Job 1: Bullhorn Monitoring (5 minutes)
scheduler.add_job(
    func=process_bullhorn_monitors,
    trigger=IntervalTrigger(minutes=5),
    id='bullhorn_monitors',
    name='Bullhorn Tearsheet Monitoring',
    replace_existing=True
)

# Job 2: Vetting Health Check (10 minutes)
scheduler.add_job(
    func=run_vetting_health_check,
    trigger=IntervalTrigger(minutes=10),
    id='vetting_health_check',
    name='Vetting System Health Check',
    replace_existing=True
)

# Job 3: File Cleanup (60 minutes)
scheduler.add_job(
    func=cleanup_temp_files,
    trigger=IntervalTrigger(minutes=60),
    id='file_cleanup',
    name='Temporary File Cleanup',
    replace_existing=True
)
```

### Scheduler Lock Mechanism

To prevent duplicate scheduled jobs in dev/production:

1. Production environment has priority
2. Lock stored in `scheduler_lock` table with 5-minute TTL
3. Lock auto-renews while scheduler is active
4. If lock expires, another instance can take over

```python
class SchedulerLock:
    @classmethod
    def acquire_lock(cls, process_id, environment, duration_minutes=5):
        # Try to get existing lock
        lock = cls.query.filter_by(id=1).first()
        
        if lock:
            # Check if lock has expired or is from different environment
            if lock.expires_at < datetime.utcnow() or lock.environment != environment:
                # Take over the lock
                lock.owner_process_id = process_id
                lock.environment = environment
                return True
        # ... create new lock if none exists
```

### Timeout Protection

Monitoring cycles have a 110-second timeout to prevent overruns:

```python
@with_timeout(seconds=110)
def process_bullhorn_monitors():
    # ... monitoring logic
```

If timeout is exceeded:
- Current cycle is abandoned
- Warning is logged
- Next cycle starts normally

---

## 8. Email Services

### Email Service Architecture

Located in `email_service.py` (~68KB, 1440 lines)

```
┌─────────────────────────────────────────────────────────────────────┐
│                       EMAIL SERVICE FLOW                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌───────────────────┐                                              │
│  │   Email Request   │                                              │
│  │   (any module)    │                                              │
│  └─────────┬─────────┘                                              │
│            │                                                        │
│            ▼                                                        │
│  ┌───────────────────────────────────────┐                         │
│  │   DUPLICATE PREVENTION CHECK          │                         │
│  │   • Check email_delivery_log          │                         │
│  │   • Same type + recipient + 5 min     │                         │
│  │   • Block if duplicate found          │                         │
│  └─────────┬─────────────────────────────┘                         │
│            │                                                        │
│            ▼                                                        │
│  ┌───────────────────────────────────────┐                         │
│  │   BUILD EMAIL                         │                         │
│  │   • HTML template rendering           │                         │
│  │   • Plain text fallback               │                         │
│  │   • Attachments if needed             │                         │
│  └─────────┬─────────────────────────────┘                         │
│            │                                                        │
│            ▼                                                        │
│  ┌───────────────────────────────────────┐                         │
│  │   SEND VIA SENDGRID                   │                         │
│  │   • API call to SendGrid              │                         │
│  │   • Get message ID for tracking       │                         │
│  └─────────┬─────────────────────────────┘                         │
│            │                                                        │
│            ▼                                                        │
│  ┌───────────────────────────────────────┐                         │
│  │   LOG DELIVERY                        │                         │
│  │   • Create email_delivery_log entry   │                         │
│  │   • Store SendGrid message ID         │                         │
│  │   • Record success/failure            │                         │
│  └───────────────────────────────────────┘                         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Email Types

| Type | Trigger | Recipients |
|------|---------|------------|
| `job_added` | New job in tearsheet | Configured notification email |
| `job_removed` | Job removed from tearsheet | Configured notification email |
| `vetting_match` | Candidate matches 80%+ | Job recruiter |
| `vetting_summary` | All matches for candidate | Notification email |
| `health_alert` | System component failure | Health alert email |
| `automated_upload` | SFTP upload complete | Configured email |
| `reference_refresh` | Reference numbers updated | Configured email |

### Collaborative Email Threading

For vetting notifications, emails use consistent Message-ID and References headers to group related messages in email clients:

```python
# Thread ID based on candidate ID
thread_id = f"vetting-{candidate_id}@jobpulse.myticas.com"

# Set headers for threading
message.headers = {
    "Message-ID": f"<{thread_id}>",
    "References": f"<{thread_id}>",
    "In-Reply-To": f"<{thread_id}>"
}
```

### SendGrid Configuration

- **From Email:** kroots@myticas.com (verified sender)
- **API Key:** Stored in `SENDGRID_API_KEY` environment variable
- **Rate Limit:** 100 emails/day on free tier

---

## 9. Job Application Forms

### Dual-Domain Support

| Domain | Branding | Logo | Recipient |
|--------|----------|------|-----------|
| `apply.myticas.com` | Myticas Consulting | myticas-logo.png | apply@myticas.com |
| `apply.stsigroup.com` | STSI Group | stsi-logo.png | apply@myticas.com |

### Application Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    JOB APPLICATION FLOW                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. USER LANDS ON APPLICATION PAGE                                  │
│     URL: /{job_id}/{job_title}/                                    │
│     Example: /123456/Senior-Software-Engineer/                      │
│                                                                     │
│  2. PAGE LOADS                                                      │
│     ├─► Detect domain (myticas vs stsigroup)                       │
│     ├─► Apply appropriate branding                                  │
│     └─► Show application form                                       │
│                                                                     │
│  3. USER UPLOADS RESUME                                             │
│     POST /parse-resume                                              │
│     ├─► Extract text from PDF/DOCX                                 │
│     ├─► Parse contact info (name, email, phone)                    │
│     └─► Auto-fill form fields                                       │
│                                                                     │
│  4. USER SUBMITS APPLICATION                                        │
│     POST /submit-application                                        │
│     ├─► Validate required fields                                   │
│     ├─► Format resume content as HTML (GPT-4o)                     │
│     ├─► Build email with attachments                               │
│     └─► Send via SendGrid to apply@myticas.com                     │
│                                                                     │
│  5. EMAIL PROCESSING (INBOUND)                                      │
│     POST /api/email/inbound                                        │
│     ├─► Parse email content                                        │
│     ├─► Extract candidate data                                     │
│     ├─► Create/update Bullhorn candidate                           │
│     ├─► Create job submission                                       │
│     └─► Queue for AI vetting                                        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### JobApplicationService

Located in `job_application_service.py` (~15KB, 342 lines)

**Key Methods:**

| Method | Purpose |
|--------|---------|
| `parse_resume(file)` | Extract contact info from uploaded resume |
| `submit_application(data, resume)` | Send application email |
| `_build_application_email_html()` | Format HTML email content |
| `_create_logo_attachment()` | Attach appropriate brand logo |

### Form Fields

| Field | Required | Validation |
|-------|----------|------------|
| First Name | Yes | Non-empty |
| Last Name | Yes | Non-empty |
| Email | Yes | Valid email format |
| Phone | No | Phone format |
| Resume | Yes | PDF or DOCX, max 10MB |
| Cover Letter | No | PDF or DOCX, max 10MB |

---

## 10. AI Candidate Vetting Module

### Overview

The AI Vetting module automatically analyzes inbound job applicants against all open positions and notifies recruiters when candidates match at 80% or higher.

### Vetting Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                    AI VETTING PIPELINE                              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  STAGE 1: CANDIDATE DETECTION                                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Search Bullhorn for new candidates with:                    │   │
│  │   • status = "Online Applicant"                             │   │
│  │   • dateAdded within last 24 hours                          │   │
│  │   • Not already in candidate_vetting_log                    │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                      │
│                              ▼                                      │
│  STAGE 2: RESUME EXTRACTION                                         │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ For each candidate:                                         │   │
│  │   • Get file attachments from Bullhorn                      │   │
│  │   • Download resume file (PDF/DOCX)                         │   │
│  │   • Extract text content                                    │   │
│  │   • Store in candidate_vetting_log.resume_text              │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                      │
│                              ▼                                      │
│  STAGE 3: JOB MATCHING (AI)                                         │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ For each job in monitored tearsheets:                       │   │
│  │   • Get job requirements (custom or AI-extracted)           │   │
│  │   • Send to GPT-4o for comparison                           │   │
│  │   • Get: match_score, match_summary, skills_match,          │   │
│  │          experience_match, gaps_identified                  │   │
│  │   • Apply location rules:                                   │   │
│  │     - Remote: Same country required                         │   │
│  │     - On-site/Hybrid: Same metro preferred, penalty for far │   │
│  │   • Store in candidate_job_match table                      │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                      │
│                              ▼                                      │
│  STAGE 4: NOTE CREATION                                             │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Create Bullhorn note on candidate with:                     │   │
│  │   • Summary of all job matches                              │   │
│  │   • Scores for each job                                     │   │
│  │   • Detailed explanations                                   │   │
│  │   • Recommendation (qualified/not qualified)                │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                      │
│                              ▼                                      │
│  STAGE 5: NOTIFICATIONS                                             │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ For matches >= 80% threshold:                               │   │
│  │   • Email job recruiter                                     │   │
│  │   • Include match details and Bullhorn link                 │   │
│  │   • Use collaborative threading                             │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Location-Aware Matching Rules

| Work Type | Location Rule | Score Impact |
|-----------|---------------|--------------|
| Remote | Same country required | -50 if different country |
| Hybrid | Same metro area preferred | -20 for same state, -40 for other |
| On-site | Same metro area preferred | -20 for same state, -40 for other |

### CandidateVettingService

Located in `candidate_vetting_service.py` (~101KB, 2111 lines)

**Key Methods:**

| Method | Purpose |
|--------|---------|
| `run_vetting_cycle()` | Main entry point |
| `detect_new_candidates()` | Find unprocessed applicants |
| `extract_resume(candidate_id)` | Get and parse resume |
| `match_candidate_to_jobs(candidate, jobs)` | AI comparison |
| `create_bullhorn_note(candidate_id, matches)` | Add note |
| `send_recruiter_notifications(matches)` | Email alerts |
| `extract_job_requirements(job)` | AI requirement extraction |

### Vetting Configuration

Stored in `vetting_config` table:

| Key | Default | Purpose |
|-----|---------|---------|
| `vetting_enabled` | false | Master on/off switch |
| `match_threshold` | 80 | Minimum score for notifications |
| `notification_email` | - | Summary email recipient |
| `batch_size` | 10 | Candidates per cycle |
| `check_interval_minutes` | 5 | Processing frequency |
| `health_alert_email` | - | System health alerts |

### AI Prompt Structure

The matching prompt includes:

```
You are an expert recruiter evaluating candidate-job fit.

CANDIDATE RESUME:
{resume_text}

JOB REQUIREMENTS:
{job_requirements}

JOB LOCATION: {city}, {state}, {country}
WORK TYPE: {work_type}

CANDIDATE LOCATION: {candidate_city}, {candidate_state}

Provide a detailed assessment including:
1. Overall match score (0-100%)
2. Skills alignment
3. Experience match
4. Location compatibility
5. Key gaps or concerns
6. Recommendation
```

---

## 11. Job Classification System

### LinkedIn Taxonomy

JobPulse classifies jobs using LinkedIn's official categories:

**Job Functions (28 categories):**
```
Accounting, Administrative, Arts and Design, Business Development,
Community and Social Services, Consulting, Customer Success and Support,
Education, Engineering, Entrepreneurship, Finance, Healthcare Services,
Human Resources, Information Technology, Legal, Marketing,
Media and Communication, Military and Protective Services, Operations,
Product Management, Program and Project Management, Purchasing,
Quality Assurance, Real Estate, Research, Sales
```

**Industries (20 categories):**
```
Accommodation Services, Administrative and Support Services, Construction,
Consumer Services, Education, Entertainment Providers,
Farming, Ranching, Forestry, Financial Services, Government Administration,
Holding Companies, Hospitals and Health Care, Manufacturing,
Oil, Gas, and Mining, Professional Services,
Real Estate and Equipment Rental Services, Retail,
Technology, Information and Media,
Transportation, Logistics, Supply Chain and Storage, Utilities, Wholesale
```

**Seniority Levels (5 categories):**
```
Executive, Director, Mid-Senior level, Entry level, Internship
```

### Classification Process

1. Job title and description sent to GPT-4o
2. AI selects one from each category
3. Results stored in XML job element
4. Used by job boards for filtering

### AIJobClassifier

Located in `job_classification_service.py` (~19KB, 338 lines)

**Key Method:**

```python
def classify_job(self, title: str, description: str) -> Dict:
    """
    Returns:
        {
            'success': True,
            'job_function': 'Information Technology',
            'industry': 'Technology, Information and Media',
            'seniority_level': 'Mid-Senior level'
        }
    """
```

---

## 12. Resume Parsing

### Three-Layer Processing Approach

```
┌─────────────────────────────────────────────────────────────────────┐
│                    RESUME PARSING PIPELINE                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  LAYER 1: TEXT EXTRACTION                                           │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ PyMuPDF (fitz) - Primary                                    │   │
│  │   • Preserves block spacing                                 │   │
│  │   • Handles complex PDF layouts                             │   │
│  │   • Falls back to PyPDF2 if unavailable                     │   │
│  │                                                             │   │
│  │ python-docx - For Word documents                            │   │
│  │   • Extracts paragraphs and formatting                      │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                      │
│                              ▼                                      │
│  LAYER 2: TEXT NORMALIZATION (Deterministic)                        │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Fixes common PDF extraction issues:                         │   │
│  │   • Unicode whitespace normalization                        │   │
│  │   • Non-breaking space cleanup                              │   │
│  │   • Ligature handling (fi, fl, etc.)                        │   │
│  │   • CamelCase boundary detection                            │   │
│  │                                                             │   │
│  │ Example: "PROFESSIONALSUMMARYAnIT"                          │   │
│  │       → "PROFESSIONAL SUMMARY An IT"                        │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                      │
│                              ▼                                      │
│  LAYER 3: AI FORMATTING (GPT-4o)                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Structures content into semantic HTML:                      │   │
│  │   • Headings (H2, H3) for sections                          │   │
│  │   • Paragraphs for text blocks                              │   │
│  │   • Bullet lists for skills/achievements                    │   │
│  │   • Proper spacing between elements                         │   │
│  │                                                             │   │
│  │ Output stored in Bullhorn's "Parsed" Resume pane            │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### ResumeParser

Located in `resume_parser.py` (~27KB, 648 lines)

**Key Methods:**

| Method | Purpose |
|--------|---------|
| `parse_resume(file, quick_mode)` | Main entry, returns parsed data |
| `_extract_text_from_pdf(file)` | PDF text extraction |
| `_extract_text_from_docx(file)` | DOCX text extraction |
| `_normalize_pdf_text(text)` | Deterministic cleanup |
| `_format_with_ai(text)` | GPT-4o HTML formatting |
| `_extract_contact_info(text)` | Regex for name, email, phone |

### Contact Info Patterns

```python
patterns = {
    'email': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
    'phone': r'(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})',
    'name': r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
}
```

---

## 13. Settings & Configuration

### Global Settings (Database)

Stored in `global_settings` table:

| Setting Key | Purpose |
|-------------|---------|
| `bullhorn_client_id` | OAuth client ID |
| `bullhorn_client_secret` | OAuth client secret |
| `bullhorn_username` | API username |
| `bullhorn_password` | API password (encrypted ref) |
| `sftp_hostname` | Job board SFTP server |
| `sftp_username` | SFTP login |
| `sftp_password` | SFTP password |
| `sftp_directory` | Upload path |
| `sftp_port` | SSH port (usually 22) |
| `notification_email` | Default alert recipient |

### Vetting Settings

Stored in `vetting_config` table (see Section 10)

### Settings UI

Access via `/settings` route (login required):

- Bullhorn credentials
- SFTP configuration
- Notification email
- Test connection buttons

---

## 14. API Endpoints Reference

### Authentication Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/login` | GET, POST | User login form |
| `/logout` | GET | End session |

### Health Check Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Basic health check |
| `/ready` | GET | Readiness probe |
| `/alive` | GET | Liveness probe |
| `/ping` | GET | Simple ping |
| `/healthz` | GET | Kubernetes-style health |

### Dashboard & UI Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Root redirect |
| `/dashboard` | GET | Main dashboard |
| `/scheduler` | GET | Scheduled jobs view |
| `/bullhorn` | GET | Bullhorn dashboard |
| `/vetting` | GET | AI vetting dashboard |
| `/settings` | GET, POST | System settings |
| `/email-logs` | GET | Email delivery logs |
| `/email-parsing` | GET | Inbound email view |
| `/ats-monitoring` | GET | Monitor management |

### Bullhorn API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/bullhorn/monitors` | GET | List monitors |
| `/api/bullhorn/activities` | GET | Activity log |
| `/api/bullhorn/connection-test` | POST | Test credentials |
| `/api/bullhorn/api-status` | GET | Connection status |
| `/bullhorn/create` | GET, POST | Create monitor |
| `/bullhorn/monitor/<id>` | GET | Monitor details |
| `/bullhorn/monitor/<id>/delete` | POST | Delete monitor |
| `/bullhorn/monitor/<id>/test` | POST | Test monitor |

### Vetting API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/vetting/save` | POST | Save vetting config |
| `/vetting/run` | POST | Manual vetting run |
| `/vetting/health-check` | POST | Run health check |
| `/vetting/test-email` | POST | Send test email |
| `/vetting/job/<id>/requirements` | POST | Update job requirements |
| `/vetting/sync-requirements` | POST | Sync all requirements |

### Job Application Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/<job_id>/<job_title>/` | GET | Application form |
| `/parse-resume` | POST | Parse uploaded resume |
| `/submit-application` | POST | Submit application |

### Email Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/email/inbound` | POST | SendGrid webhook |
| `/api/email/parsed` | GET | Parsed emails list |
| `/api/email/stats` | GET | Email statistics |
| `/api/email-logs` | GET | Delivery log API |

### System API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/refresh-reference-numbers` | POST | Manual refresh |
| `/api/trigger/job-sync` | POST | Force monitoring |
| `/api/trigger/file-cleanup` | POST | Cleanup temp files |
| `/api/trigger/health-check` | POST | Force health check |
| `/api/system/health` | GET | System health JSON |
| `/api/diagnostic/automation-status` | GET | Scheduler status |

---

## 15. User Interface Guide

### Template Files

| Template | Route | Purpose |
|----------|-------|---------|
| `login.html` | `/login` | Authentication page |
| `dashboard.html` | `/dashboard` | Main overview |
| `scheduler.html` | `/scheduler` | Scheduled jobs |
| `bullhorn.html` | `/bullhorn` | Monitor dashboard |
| `bullhorn_create.html` | `/bullhorn/create` | Add monitor |
| `bullhorn_details.html` | `/bullhorn/monitor/<id>` | Monitor details |
| `bullhorn_settings.html` | `/bullhorn/settings` | Bullhorn config |
| `vetting_settings.html` | `/vetting` | Vetting config (dev) |
| `vetting_coming_soon.html` | `/vetting` | Vetting view (prod) |
| `settings.html` | `/settings` | System settings |
| `email_logs.html` | `/email-logs` | Delivery logs |
| `email_parsing.html` | `/email-parsing` | Inbound emails |
| `ats_monitoring.html` | `/ats-monitoring` | Monitor management |
| `apply.html` | `/<job>/` | Myticas application |
| `apply_stsi.html` | `/<job>/` | STSI application |
| `sample_notes.html` | `/vetting/sample-notes` | Note examples |

### UI Framework

- **CSS Framework:** Bootstrap 5 (dark theme)
- **Icons:** Font Awesome 6.0
- **JavaScript:** Vanilla JS for interactions
- **Template Engine:** Jinja2

### Custom Filters

```python
@app.template_filter('eastern_time')
def eastern_time_filter(utc_dt, format_string='%b %d, %Y at %I:%M %p %Z'):
    """Convert UTC to Eastern Time for display"""
    
@app.template_filter('eastern_short')
def eastern_short_filter(utc_dt):
    """Short format: Jan 15, 2:30 PM"""
    
@app.template_filter('eastern_datetime')
def eastern_datetime_filter(utc_dt):
    """Full datetime format"""
```

---

## 16. Security & Authentication

### Authentication System

- **Framework:** Flask-Login
- **Session Storage:** Server-side (Flask session)
- **Password Hashing:** Werkzeug's generate_password_hash (default method)
- **Session Timeout:** Default Flask session lifetime

### Protected Routes

All routes except the following require login:

- `/health`, `/ready`, `/alive`, `/ping`, `/healthz`
- `/<job_id>/<job_title>/` (application forms)
- `/parse-resume`, `/submit-application`
- `/api/email/inbound` (webhook)

### Credential Storage

| Credential Type | Storage Method |
|-----------------|----------------|
| User passwords | Hashed in `user` table |
| Bullhorn OAuth | `global_settings` table |
| SFTP passwords | `global_settings` table |
| API keys | Environment variables |

### Environment Secrets

The following are stored as Replit secrets (never in code):

- `BULLHORN_PASSWORD`
- `SENDGRID_API_KEY`
- `OPENAI_API_KEY`
- `SESSION_SECRET`
- `OAUTH_REDIRECT_BASE_URL`

---

## 17. Health Monitoring

### System Health Components

```
┌─────────────────────────────────────────────────────────────────────┐
│                    HEALTH MONITORING SYSTEM                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  COMPONENT                  CHECK                    INTERVAL       │
│  ─────────                  ─────                    ────────       │
│  Bullhorn API               OAuth token validity     10 min         │
│  OpenAI API                 API key validation       10 min         │
│  PostgreSQL                 Connection test          10 min         │
│  APScheduler                Job status check         10 min         │
│  Production Environment     HTTP ping                5 min          │
│                                                                     │
│  ALERT CONDITIONS                                                   │
│  ─────────────────                                                  │
│  • Any component unhealthy → Email alert                            │
│  • Alert throttling: 1 hour between alerts                          │
│  • Recovery notification when all healthy again                     │
│                                                                     │
│  DASHBOARD DISPLAY                                                  │
│  ─────────────────                                                  │
│  • Real-time status indicators                                      │
│  • Last check timestamps                                            │
│  • Error details if unhealthy                                       │
│  • Stats: candidates processed, emails sent                         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Health Check Endpoints

| Endpoint | Response | Use Case |
|----------|----------|----------|
| `/health` | `{"status": "healthy"}` | Basic alive check |
| `/ready` | `{"status": "ready"}` | App ready to serve |
| `/alive` | `{"status": "alive"}` | Kubernetes liveness |
| `/api/system/health` | Full component status | Dashboard |

---

## 18. Troubleshooting Guide

### Common Issues

#### 1. Bullhorn Authentication Failed

**Symptoms:** "Received HTML login page instead of JSON data"

**Causes:**
- Expired OAuth tokens
- Invalid credentials
- Bullhorn maintenance

**Solutions:**
1. Check Bullhorn credentials in Settings
2. Wait 15 minutes for token refresh
3. Test connection with "Test Connection" button

#### 2. XML Upload Failed

**Symptoms:** SFTP errors in logs

**Causes:**
- Invalid SFTP credentials
- Network issues
- Server full

**Solutions:**
1. Test SFTP connection in Settings
2. Check SFTP server status
3. Verify credentials

#### 3. AI Vetting Not Processing

**Symptoms:** Candidates not appearing in vetting log

**Causes:**
- Vetting disabled
- OpenAI API issues
- No new candidates

**Solutions:**
1. Check `vetting_enabled` is true
2. Verify OpenAI API key
3. Run manual vetting cycle

#### 4. Scheduler Lock Stuck

**Symptoms:** "Another monitoring process is running"

**Causes:**
- Previous process crashed
- Lock not released

**Solutions:**
1. Wait 5 minutes for lock expiration
2. Check scheduler_lock table
3. Manually clear expired lock

#### 5. Email Notifications Not Sending

**Symptoms:** Emails logged but not received

**Causes:**
- SendGrid rate limit
- Invalid recipient
- Spam filtering

**Solutions:**
1. Check email_delivery_log for errors
2. Verify SendGrid quota
3. Check spam folder

### Log Locations

| Component | Log Location |
|-----------|--------------|
| Flask App | Console output (Replit logs) |
| Scheduler | Console output with [APScheduler] prefix |
| Bullhorn | Console output with [BullhornService] prefix |
| Email | email_delivery_log table |

### Diagnostic Commands

```python
# Check scheduler status
GET /api/diagnostic/automation-status

# Check Bullhorn connection
POST /api/bullhorn/connection-test

# Check system health
GET /api/system/health

# Force monitoring cycle
POST /api/trigger/job-sync
```

---

## 19. File Structure Reference

### Project Root

```
JobPulse/
├── app.py                           # Main Flask application (~344KB)
├── main.py                          # Entry point
├── models.py                        # Database models (~32KB)
├── seed_database.py                 # Database seeding (~35KB)
│
├── # Services
├── bullhorn_service.py              # Bullhorn API integration (~77KB)
├── email_service.py                 # SendGrid email service (~68KB)
├── email_inbound_service.py         # Inbound email parsing (~50KB)
├── candidate_vetting_service.py     # AI vetting engine (~101KB)
├── incremental_monitoring_service.py # Tearsheet monitoring (~53KB)
├── job_application_service.py       # Application processing (~15KB)
├── job_classification_service.py    # AI job classification (~19KB)
├── resume_parser.py                 # Resume extraction (~27KB)
├── xml_integration_service.py       # XML building
├── simplified_xml_generator.py      # Clean XML generation (~17KB)
├── xml_processor.py                 # XML processing utilities
├── ftp_service.py                   # SFTP upload service (~11KB)
├── tearsheet_config.py              # Tearsheet configuration (~4KB)
│
├── # Templates
├── templates/
│   ├── base.html                    # Base template
│   ├── login.html                   # Login page
│   ├── dashboard.html               # Main dashboard
│   ├── bullhorn.html                # Bullhorn dashboard
│   ├── vetting_settings.html        # Vetting config
│   ├── apply.html                   # Myticas application
│   ├── apply_stsi.html              # STSI application
│   └── ...                          # 21 templates total
│
├── # Static Files
├── static/
│   ├── css/                         # Custom styles
│   ├── js/                          # JavaScript files
│   └── images/                      # Logos, icons
│
├── # Data Files
├── myticas-job-feed-v2.xml          # Current job feed
├── linkedin_categories.md           # LinkedIn taxonomy reference
│
├── # Documentation
├── JOBPULSE_TECHNICAL_DOCUMENTATION.md  # This document
├── JOBPULSE_MULTI_TENANT_ROADMAP.md     # Multi-tenant planning
├── replit.md                        # Project configuration
│
├── # Configuration
├── .replit                          # Replit configuration
├── pyproject.toml                   # Python dependencies
└── requirements.txt                 # Pip requirements
```

### Key File Sizes

| File | Lines | Size | Purpose |
|------|-------|------|---------|
| app.py | ~7,600 | 344KB | Main application with all routes |
| candidate_vetting_service.py | ~2,100 | 101KB | AI vetting engine |
| bullhorn_service.py | ~1,700 | 77KB | Bullhorn API |
| email_service.py | ~1,400 | 68KB | Email handling |
| incremental_monitoring_service.py | ~1,166 | 53KB | Monitoring |
| models.py | ~692 | 32KB | 21 database models |

---

## Document Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | Feb 1, 2026 | JobPulse Documentation | Initial comprehensive documentation |

---

*This document is confidential and intended for internal reference.*
*For questions, contact the development team.*
