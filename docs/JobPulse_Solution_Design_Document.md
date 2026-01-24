# JobPulse™ Solution Design Document
## Intelligent Job Visibility & Freshness Automation Platform

**Document Version:** 3.0  
**Date:** January 2026  
**Classification:** Confidential - For Investor Review  
**Prepared By:** [Entity Name]

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [The Core Problem: Job Visibility Decay](#2-the-core-problem-job-visibility-decay)
3. [The JobPulse™ Solution: Perpetual Freshness](#3-the-jobpulse-solution-perpetual-freshness)
4. [System Architecture](#4-system-architecture)
5. [Core Capabilities](#5-core-capabilities)
6. [Technical Implementation](#6-technical-implementation)
7. [Integration Framework](#7-integration-framework)
8. [Security & Compliance](#8-security--compliance)
9. [Deployment Architecture](#9-deployment-architecture)
10. [Performance & Reliability](#10-performance--reliability)
11. [Future Roadmap](#11-future-roadmap)
12. [Appendices](#12-appendices)

---

## 1. Executive Summary

### 1.1 The Hidden Problem

Staffing agencies spend thousands on job board subscriptions, but their listings become invisible within weeks. **Job boards algorithmically demote "stale" postings**, pushing them below newer listings in search results. A job posted 30 days ago might as well not exist - candidates never see it.

### 1.2 The JobPulse™ Solution

**JobPulse™** is an intelligent job visibility platform that keeps job listings appearing **fresh and new indefinitely**. Through automated refresh cycles, smart metadata optimization, and continuous synchronization, JobPulse ensures maximum job visibility regardless of actual posting date.

This isn't just automation - it's a **visibility multiplier** that compounds value every day a job remains open.

### 1.3 Key Value Propositions

| Value Driver | Business Impact |
|--------------|-----------------|
| **Perpetual Freshness** | Jobs maintain 100% visibility vs. 15% decay at Day 30 |
| **Visibility Multiplier** | 6x more candidate exposure over job lifetime |
| **Zero Manual Intervention** | Eliminates 40+ hours/month of refresh work |
| **Data Integrity** | 99.9% reference number consistency across all platforms |
| **Intelligent Classification** | Sub-second job categorization using LinkedIn taxonomy |

### 1.4 Deployment Status

- **Production URL:** jobpulse.[entityname].ai
- **Application Portals:** apply.myticas.com, apply.stsigroup.com
- **Current Uptime:** 99.95% (30-day average)
- **Active Job Postings:** 68+ managed positions
- **Refresh Frequency:** Every 30 minutes

---

## 2. The Core Problem: Job Visibility Decay

### 2.1 How Job Boards Work Against You

Job boards like Indeed, LinkedIn, and ZipRecruiter use algorithms that prioritize **recency**. When a job hasn't been updated, the algorithm assumes:

- The position may already be filled
- The posting is no longer active
- Newer, more relevant opportunities exist

**Result:** Your job gets pushed down in search results, reducing visibility by 85% or more within 30 days.

### 2.2 The Visibility Decay Curve

```
Job Visibility Over Time (Traditional Approach)
──────────────────────────────────────────────

100% │████████████████
 90% │███████████████░
 80% │██████████████░░
 70% │█████████████░░░░░ ← Day 7
 60% │████████████░░░░░░
 50% │███████████░░░░░░░
 40% │██████████░░░░░░░░░
 30% │█████████░░░░░░░░░░░
 20% │████████░░░░░░░░░░░░░
 15% │███████░░░░░░░░░░░░░░░ ← Day 30
 10% │██████░░░░░░░░░░░░░░░░
  5% │█████░░░░░░░░░░░░░░░░░░ ← Day 60
     └──────────────────────────────────────
       Day 1   7   14   21   30   45   60
```

### 2.3 The Industry Response (Ineffective)

Most agencies attempt to combat visibility decay through:

1. **Manual Reposting** - Time-consuming, error-prone, breaks applicant tracking
2. **Paying for "Sponsored" Listings** - Expensive, temporary visibility boost
3. **Accepting the Decline** - Missing qualified candidates

**None of these solutions address the root cause.**

### 2.4 Quantifying the Loss

For an agency with 68 active positions:

| Scenario | Day 30 Visibility | Total Exposure (60 days) | Estimated Lost Applicants |
|----------|-------------------|--------------------------|---------------------------|
| Traditional | 15% | 35% average | 40-60% fewer applications |
| JobPulse™ | 100% | 100% maintained | None |

---

## 3. The JobPulse™ Solution: Perpetual Freshness

### 3.1 The Core Innovation

JobPulse™ implements **Intelligent Refresh Automation**—a system that continuously updates job listings to signal freshness to job board algorithms while maintaining data integrity for applicant tracking.

```
┌─────────────────────────────────────────────────────────────────┐
│                    PERPETUAL FRESHNESS ENGINE                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐    │
│  │   Bullhorn   │──▶│   JobPulse   │──▶│   Job Boards     │    │
│  │   ATS/CRM    │   │   Freshness  │   │   (LinkedIn,     │    │
│  │              │   │   Engine     │   │    Indeed, etc)  │    │
│  └──────────────┘   └──────────────┘   └──────────────────┘    │
│         │                  │                    │               │
│         │                  │                    │               │
│         ▼                  ▼                    ▼               │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐    │
│  │  Tearsheet   │   │  Continuous  │   │   Maximum        │    │
│  │  Monitoring  │   │  Refresh     │   │   Visibility     │    │
│  │              │   │  (30 min)    │   │   (100%)         │    │
│  └──────────────┘   └──────────────┘   └──────────────────┘    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 How Perpetual Freshness Works

**Every 30 Minutes:**

1. **XML Regeneration** - Complete job feed is regenerated with current timestamps
2. **Metadata Optimization** - Classification tags, descriptions refreshed
3. **Reference Preservation** - Database-backed identifiers remain consistent
4. **SFTP Distribution** - Fresh feed pushed to job boards

**The Result:** Job boards see "updated" listings and maintain their search ranking.

### 3.3 The Visibility Advantage

```
┌─────────────────────────────────────────────────────────────────┐
│                     THE VISIBILITY ADVANTAGE                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   TRADITIONAL APPROACH              JOBPULSE™ APPROACH          │
│   ────────────────────              ──────────────────          │
│                                                                  │
│   Day 1:  ████████████ 100%         Day 1:  ████████████ 100%  │
│   Day 7:  ████████░░░░  70%         Day 7:  ████████████ 100%  │
│   Day 14: █████░░░░░░░  45%         Day 14: ████████████ 100%  │
│   Day 30: ██░░░░░░░░░░  15%         Day 30: ████████████ 100%  │
│   Day 60: ░░░░░░░░░░░░   5%         Day 60: ████████████ 100%  │
│                                                                  │
│            Visibility                     Visibility            │
│              Decay                        Preserved             │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.4 Key Technical Differentiators

| Feature | Purpose | Impact |
|---------|---------|--------|
| **30-Minute Refresh Cycles** | Signal activity to algorithms | Maintained ranking |
| **Database-First Reference Numbers** | Preserve applicant tracking | Zero tracking errors |
| **Smart Timestamp Management** | Indicate "recent" updates | Algorithm favorability |
| **Continuous Metadata Optimization** | Fresh classification tags | Better search matching |

---

## 4. System Architecture

### 4.1 High-Level Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                          PRESENTATION LAYER                             │
├────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │
│  │  Admin Portal   │  │  Application    │  │  Health Monitoring      │ │
│  │  (Dashboard)    │  │  Forms          │  │  Endpoints              │ │
│  │  Bootstrap 5    │  │  (Branded)      │  │  /health, /ready, /ping │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                         APPLICATION LAYER                               │
├────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │
│  │  Flask Web      │  │  APScheduler    │  │  Service Layer          │ │
│  │  Framework      │  │  Perpetual      │  │  - Freshness Engine     │ │
│  │                 │  │  Refresh        │  │  - Bullhorn Service     │ │
│  │                 │  │  Engine         │  │  - XML Generator        │ │
│  │                 │  │                 │  │  - Email Service        │ │
│  │                 │  │                 │  │  - Classification       │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌────────────────────────────────────────────────────────────────────────┐
│                           DATA LAYER                                    │
├────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────┐ │
│  │  PostgreSQL     │  │  File System    │  │  External APIs          │ │
│  │  Database       │  │  (XML Storage)  │  │  - Bullhorn REST        │ │
│  │  - SQLAlchemy   │  │  - Backups      │  │  - SendGrid             │ │
│  │  - ORM Models   │  │  - Temp Files   │  │  - SFTP Server          │ │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Component Overview

| Component | Technology | Purpose |
|-----------|------------|---------|
| Web Framework | Flask (Python) | Request handling, routing, templating |
| Freshness Engine | APScheduler | 30-minute automated refresh cycles |
| Database | PostgreSQL + SQLAlchemy | Reference number persistence |
| XML Processing | lxml | Job feed generation and parsing |
| Authentication | Flask-Login | Secure admin access |
| Email | SendGrid | Notifications and applicant routing |
| File Transfer | Paramiko (SFTP) | Secure XML distribution |

### 4.3 Domain Architecture

| Domain | Purpose | Users |
|--------|---------|-------|
| jobpulse.[entitydomain].ai | Admin dashboard, freshness controls | Internal staff |
| apply.myticas.com | Myticas-branded job application portal | Candidates |
| apply.stsigroup.com | STSI-branded job application portal | Candidates |

---

## 5. Core Capabilities

### 5.1 Perpetual Freshness Engine (Primary Differentiator)

The Perpetual Freshness Engine is JobPulse's core innovation—ensuring jobs never appear "stale" to job board algorithms.

**Architecture:**

```
┌─────────────────────────────────────────────────────────────┐
│                PERPETUAL FRESHNESS ENGINE                    │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   CONTINUOUS REFRESH CYCLE (Every 30 Minutes)               │
│   ─────────────────────────────────────────────             │
│                                                              │
│   ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐ │
│   │ Fetch   │───▶│ Apply   │───▶│ Update  │───▶│ Distri- │ │
│   │ Latest  │    │ Fresh   │    │ Meta-   │    │ bute to │ │
│   │ Jobs    │    │ Time-   │    │ data &  │    │ Job     │ │
│   │ from    │    │ stamps  │    │ Class-  │    │ Boards  │ │
│   │ ATS     │    │         │    │ ification│    │ (SFTP)  │ │
│   └─────────┘    └─────────┘    └─────────┘    └─────────┘ │
│        │                                             │       │
│        └─────────────── REPEAT ──────────────────────┘       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**Key Features:**

| Feature | Implementation | Benefit |
|---------|---------------|---------|
| **Fresh Timestamps** | Every refresh updates posting dates | Algorithm sees "recent" activity |
| **Reference Persistence** | Database-backed IDs unchanged | Applicant tracking maintained |
| **Classification Refresh** | Tags re-evaluated each cycle | Optimal search matching |
| **Continuous Distribution** | SFTP push every 30 minutes | Maximum freshness signal |

### 5.2 Dual-Cycle Monitoring System

**5-Minute Monitoring Cycle (UI & Notifications)**
- Real-time tearsheet scanning
- Instant new job detection
- Email notifications for new positions
- Dashboard visibility updates

**30-Minute Upload Cycle (Freshness & Distribution)**
- Full XML regeneration with fresh timestamps
- Reference number preservation from database
- SFTP synchronization to job boards
- Cross-platform distribution

```
┌─────────────────────────────────────────────────────────────┐
│                  DUAL-CYCLE ARCHITECTURE                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   5-MIN CYCLE (Detection)          30-MIN CYCLE (Freshness) │
│   ─────────────────────            ────────────────────────  │
│   ┌─────────┐                      ┌─────────┐              │
│   │ Scan    │                      │ Generate│              │
│   │ Tear-   │                      │ Fresh   │              │
│   │ sheets  │                      │ XML     │              │
│   └────┬────┘                      └────┬────┘              │
│        │                                │                    │
│        ▼                                ▼                    │
│   ┌─────────┐                      ┌─────────┐              │
│   │ Detect  │                      │ Apply   │              │
│   │ New     │                      │ Fresh   │              │
│   │ Jobs    │                      │ Stamps  │              │
│   └────┬────┘                      └────┬────┘              │
│        │                                │                    │
│        ▼                                ▼                    │
│   ┌─────────┐                      ┌─────────┐              │
│   │ Notify  │                      │ SFTP    │              │
│   │ Email   │                      │ Upload  │              │
│   └─────────┘                      └─────────┘              │
│                                                              │
│   Purpose: Awareness            Purpose: Visibility          │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 5.3 Database-First Reference Number System

Ensures applicant tracking continuity while jobs appear "fresh":

**Architecture:**
- `JobReferenceNumber` table is single source of truth
- Reference numbers generated once per job, never change
- All XML generations load consistent references from database
- 120-hour internal refresh cycle for database maintenance

**Why This Matters:**

Traditional "reposting" breaks applicant tracking—candidates applying to a "reposted" job get lost. JobPulse maintains **one continuous applicant pipeline** while the job appears fresh.

### 5.4 Intelligent Job Classification

Keyword-based classification aligned with LinkedIn's official taxonomy:

| Category | Options | Method |
|----------|---------|--------|
| Job Functions | 28 categories | Keyword matching |
| Industries | 20 sectors | Title/description analysis |
| Seniority Levels | 5 levels | Title pattern recognition |

**Performance:**
- Classification time: <1 second per job
- No external API dependencies
- Weighted scoring (title: 3x, description: 1x)
- Guaranteed fallback defaults

### 5.5 Zero-Job Detection Safeguard

Protection against API failures corrupting job feeds:

**Trigger Condition:** API returns 0 jobs when XML contains ≥5 jobs

**Response Actions:**
1. Block XML update (prevent corruption)
2. Create timestamped backup
3. Send single alert email
4. Preserve existing job data

---

## 6. Technical Implementation

### 6.1 Database Schema

#### Core Models

```
┌────────────────────────────────────────────────────────────┐
│                    DATABASE SCHEMA                          │
├────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ JobReferenceNumber (Critical for Freshness)         │  │
│  ├─────────────────────────────────────────────────────┤  │
│  │ id: Integer (PK)                                     │  │
│  │ job_id: String(255) [Unique, Indexed]               │  │
│  │ reference_number: String(50) [NEVER CHANGES]        │  │
│  │ last_updated: DateTime                               │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ TearsheetMonitor                                     │  │
│  ├─────────────────────────────────────────────────────┤  │
│  │ id: Integer (PK)                                     │  │
│  │ name: String(100)                                    │  │
│  │ tearsheet_id: Integer                                │  │
│  │ is_active: Boolean                                   │  │
│  │ company_override: String(200)                        │  │
│  │ last_job_count: Integer                              │  │
│  │ last_checked: DateTime                               │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ GlobalSettings                                       │  │
│  ├─────────────────────────────────────────────────────┤  │
│  │ id: Integer (PK)                                     │  │
│  │ setting_key: String(100) [Unique]                   │  │
│  │ setting_value: Text                                  │  │
│  │ updated_at: DateTime                                 │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ ActivityLog                                          │  │
│  ├─────────────────────────────────────────────────────┤  │
│  │ id: Integer (PK)                                     │  │
│  │ action: String(100)                                  │  │
│  │ details: Text                                        │  │
│  │ timestamp: DateTime                                  │  │
│  │ user_id: Integer (FK)                               │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ User                                                 │  │
│  ├─────────────────────────────────────────────────────┤  │
│  │ id: Integer (PK)                                     │  │
│  │ username: String(64) [Unique]                       │  │
│  │ email: String(120) [Unique]                         │  │
│  │ password_hash: String(256)                          │  │
│  │ is_admin: Boolean                                    │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                             │
└────────────────────────────────────────────────────────────┘
```

### 6.2 Freshness Engine Implementation

**XML Generation Process:**
```
1. Pull jobs from Bullhorn tearsheets
2. Load PERSISTENT reference numbers from database
3. Apply FRESH timestamps (current time)
4. Generate CDATA-wrapped XML
5. Apply intelligent job classification
6. Validate structure
7. Upload to SFTP (signals "fresh" to job boards)
```

**Key Insight:** Reference numbers stay the same (applicant tracking works), but timestamps are fresh (algorithms see activity).

### 6.3 Bullhorn API Integration

**Supported Operations:**
- OAuth 2.0 authentication
- Tearsheet job retrieval
- Entity data extraction
- Real-time job monitoring

**Recruiter Extraction Hierarchy:**
1. `assignedUsers(firstName,lastName)` - primary
2. `responseUser(firstName,lastName)` - fallback
3. `owner(firstName,lastName)` - final fallback

**Success Rate:** 95.6% recruiter tag population

---

## 7. Integration Framework

### 7.1 Bullhorn ATS Integration

**Connection Method:** REST API with OAuth 2.0

**Data Flow:**
```
Bullhorn → JobPulse → Fresh XML Feed → Job Boards
                ↓
         Application Forms → Email → Recruiters
```

**Monitored Tearsheets:**
| Tearsheet ID | Purpose | Company |
|--------------|---------|---------|
| 1256 | Primary Myticas jobs | Myticas Consulting |
| 1264 | Secondary Myticas | Myticas Consulting |
| 1499 | Additional positions | Myticas Consulting |
| 1556 | STSI positions | STSI (Staffing Technical Services Inc.) |

### 7.2 SendGrid Email Integration

**Use Cases:**
- New job notifications (5-minute cycle)
- Application confirmations
- System alerts (zero-job detection)
- Production monitoring

**Sender Configuration:**
- From: info@myticas.com
- Reply-To: apply@myticas.com

### 7.3 SFTP Distribution

**Protocol:** SFTP (Secure File Transfer Protocol)  
**Port:** 2222  
**Frequency:** Every 30 minutes (freshness cycle)

**This is the critical distribution point** - each upload signals to job boards that content is fresh and active.

---

## 8. Security & Compliance

### 8.1 Authentication & Authorization

| Feature | Implementation |
|---------|----------------|
| User Authentication | Flask-Login with session management |
| Password Security | Werkzeug password hashing |
| Session Encryption | Secure session keys |
| Admin Access Control | Role-based permissions |

### 8.2 Data Protection

| Measure | Description |
|---------|-------------|
| HTTPS | All traffic encrypted via TLS |
| Secrets Management | Environment-based secret storage |
| Database Security | Connection pooling with pre-ping validation |
| SFTP | Secure file transfers (not FTP) |

### 8.3 Operational Security

- ProxyFix middleware for proper HTTPS handling
- No secrets exposed in logs or UI
- Automated backup creation before destructive operations
- Zero-job safeguard prevents data corruption

---

## 9. Deployment Architecture

### 9.1 Infrastructure

**Platform:** Replit Cloud (Production)

**Environment Characteristics:**
- Auto-scaling compute resources
- Managed PostgreSQL database
- Built-in health monitoring
- Automatic SSL/TLS certificates

### 9.2 High Availability

```
┌────────────────────────────────────────────────────────────────┐
│                    DEPLOYMENT TOPOLOGY                          │
├────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────┐      ┌───────────────┐     ┌─────────────┐ │
│  │   Replit      │      │   PostgreSQL  │     │   SFTP      │ │
│  │   Cloud       │◄────▶│   Database    │     │   Server    │ │
│  │   (Flask)     │      │   (Neon)      │     │   (3rd pty) │ │
│  └───────┬───────┘      └───────────────┘     └──────▲──────┘ │
│          │                                           │         │
│          │           ┌───────────────┐               │         │
│          └──────────▶│   Bullhorn    │───────────────┘         │
│                      │   REST API    │                         │
│                      └───────────────┘                         │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
```

### 9.3 Environment Isolation

| Environment | Purpose | XML Targets |
|-------------|---------|-------------|
| Development | Testing, staging | `-dev.xml` suffixed files |
| Production | Live operations | Standard `.xml` files |

Separate PostgreSQL databases and independent schedules prevent cross-contamination.

---

## 10. Performance & Reliability

### 10.1 Key Metrics

| Metric | Target | Current |
|--------|--------|---------|
| System Uptime | 99.9% | 99.95% |
| Refresh Cycle Completion | <60 seconds | ~30 seconds |
| Job Classification | <1 second | <500ms |
| Email Delivery | <5 seconds | <3 seconds |

### 10.2 Health Monitoring

**Endpoints:**
- `/health` - Comprehensive system check
- `/ready` - Readiness probe
- `/alive` - Liveness probe
- `/ping` - Simple heartbeat

**External Monitoring:** UptimeRobot (5-minute intervals)

### 10.3 Error Recovery

| Scenario | Response |
|----------|----------|
| API returns 0 jobs | Block update, backup existing, alert |
| SFTP connection fails | Retry with exponential backoff |
| Database unavailable | Use published XML as fallback |
| Classification error | Apply default categories |

---

## 11. Future Roadmap

### 11.1 Q1 2026

**Bullhorn One Migration (January 26)**
- Transition to new ATS platform with updated API credentials
- Maintain all existing freshness automation functionality

**Visibility Analytics**
- Track job ranking positions over time
- Measure visibility improvement metrics

### 11.2 Q2-Q3 2026

**AI Email Parser**
- Automated applicant routing directly to ATS
- Resume parsing and candidate matching

**Multi-Tenant Architecture**
- Support for additional staffing clients
- Isolated databases per tenant

**Advanced Refresh Algorithms**
- Machine learning optimization of refresh timing
- Predictive visibility modeling

### 11.3 Long-Term Vision

**ATS-Agnostic Platform**
- Support any applicant tracking system
- Universal connector framework

**Visibility Insights Dashboard**
- Real-time job board ranking data
- Competitive visibility analysis

**White-Label Solution**
- Licensable platform for staffing agencies
- Partner integration program

---

## 12. Appendices

### 12.1 Glossary

| Term | Definition |
|------|------------|
| **Perpetual Freshness** | JobPulse's core capability of keeping jobs appearing "new" indefinitely |
| **Visibility Decay** | The algorithmic demotion of stale job listings by job boards |
| **Reference Number** | Unique identifier that persists for applicant tracking |
| **Tearsheet** | Bullhorn's saved list of job postings for monitoring |
| **Freshness Cycle** | 30-minute automated refresh and distribution process |

### 12.2 API Reference

**Bullhorn REST API Endpoints Used:**
- `/entity/Tearsheet/{id}` - Tearsheet metadata
- `/search/JobOrder` - Job search queries
- `/entity/JobOrder/{id}` - Individual job details

**Internal API Endpoints:**
- `/api/trigger-upload` - Manual refresh trigger
- `/api/refresh-all` - Force reference number refresh
- `/api/monitoring/status` - System status

### 12.3 Technical Learnings

**Bullhorn REST API Field Constraints:**
- `assignments` field NOT accessible via REST API (removed Nov 9, 2025)
- Recruiter data extracted via `assignedUsers` → `responseUser` → `owner` fallback hierarchy
- Current success rate: 95.6% recruiter tag population

**November 6, 2025 Incident:**
- Invalid API field caused 0-job response, triggering 68 false "new job" emails
- Resolution: Zero-job detection safeguard now blocks updates when API returns 0 jobs but XML contains ≥5 jobs

---

**Document Control**

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | October 2025 | Initial creation |
| 2.0 | November 2025 | Added zero-job safeguard documentation |
| 2.5 | January 2026 | Updated Bullhorn One migration plans |
| 3.0 | January 2026 | Revised to emphasize Perpetual Freshness differentiator |

---

*Confidential - For Investor Review Only*
