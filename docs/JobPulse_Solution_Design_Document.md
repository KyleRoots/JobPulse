# JobPulse™ Solution Design Document
## XML Job Feed Automation & ATS Integration Platform

**Document Version:** 2.5  
**Date:** January 2026  
**Classification:** Confidential - For Investor Review  
**Prepared By:** Myticas Consulting / Lyntrix Technology Solutions

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Business Problem & Solution](#2-business-problem--solution)
3. [System Architecture](#3-system-architecture)
4. [Core Capabilities](#4-core-capabilities)
5. [Technical Implementation](#5-technical-implementation)
6. [Integration Framework](#6-integration-framework)
7. [Security & Compliance](#7-security--compliance)
8. [Deployment Architecture](#8-deployment-architecture)
9. [Performance & Reliability](#9-performance--reliability)
10. [Future Roadmap](#10-future-roadmap)
11. [Appendices](#11-appendices)

---

## 1. Executive Summary

### 1.1 Product Overview

**JobPulse™** is a comprehensive enterprise-grade job feed automation platform designed to streamline recruitment operations by automating XML job feed management, synchronizing data with Applicant Tracking Systems (ATS), and providing intelligent job classification capabilities.

The platform serves as the critical infrastructure layer between Bullhorn ATS/CRM and external job board distribution networks, ensuring accurate, real-time job listings while eliminating manual data entry and reducing operational overhead.

### 1.2 Key Value Propositions

| Value Driver | Business Impact |
|--------------|-----------------|
| **Automation** | Eliminates 40+ hours/month of manual XML processing |
| **Data Integrity** | 99.9% reference number consistency across all job boards |
| **Real-Time Sync** | 5-minute monitoring cycles for instant job updates |
| **Intelligent Classification** | Sub-second job categorization using LinkedIn taxonomy |
| **Multi-Brand Support** | Unified platform serving Myticas and STSI brands |

### 1.3 Deployment Status

- **Production URL:** jobpulse.lyntrix.ai
- **Application Portals:** apply.myticas.com, apply.stsigroup.com
- **Current Uptime:** 99.95% (30-day average)
- **Active Job Postings:** 68+ managed positions

---

## 2. Business Problem & Solution

### 2.1 Industry Challenge

Staffing and recruitment agencies face significant operational challenges in managing job postings across multiple job boards:

1. **Manual XML Management:** Traditional processes require manual editing of XML feed files, leading to errors and inconsistencies
2. **Reference Number Drift:** Job reference numbers frequently revert to outdated values, breaking applicant tracking
3. **Delayed Updates:** Manual processes create lag between ATS changes and job board visibility
4. **Classification Complexity:** Proper job categorization for LinkedIn and other platforms requires deep industry knowledge
5. **Multi-Brand Complexity:** Managing separate job feeds for different business brands increases operational burden

### 2.2 JobPulse™ Solution

JobPulse™ addresses these challenges through a unified automation platform:

```
┌─────────────────────────────────────────────────────────────────┐
│                    JOBPULSE™ AUTOMATION ENGINE                   │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐    │
│  │   Bullhorn   │──▶│   JobPulse   │──▶│   Job Boards     │    │
│  │   ATS/CRM    │   │   Platform   │   │   (LinkedIn,     │    │
│  │              │   │              │   │    Indeed, etc)  │    │
│  └──────────────┘   └──────────────┘   └──────────────────┘    │
│         │                  │                    │               │
│         ▼                  ▼                    ▼               │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐    │
│  │  Tearsheet   │   │  Intelligent │   │   Applicant      │    │
│  │  Monitoring  │   │  XML Engine  │   │   Routing        │    │
│  └──────────────┘   └──────────────┘   └──────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
```

### 2.3 Quantifiable Benefits

| Metric | Before JobPulse™ | After JobPulse™ | Improvement |
|--------|------------------|-----------------|-------------|
| XML Update Time | 2-4 hours/week | Automated | 100% reduction |
| Reference Number Errors | 15-20% | <0.1% | 99%+ reduction |
| Job Board Sync Delay | 24-48 hours | 30 minutes | 96% faster |
| Classification Accuracy | 70% manual | 95%+ automated | 35% improvement |
| Applicant Data Entry | Manual | Auto-parsed | 80% reduction |

---

## 3. System Architecture

### 3.1 High-Level Architecture

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
│  │  Framework      │  │  Background     │  │  - Bullhorn Service     │ │
│  │                 │  │  Processing     │  │  - XML Generator        │ │
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

### 3.2 Component Overview

| Component | Technology | Purpose |
|-----------|------------|---------|
| Web Framework | Flask (Python) | Request handling, routing, templating |
| Database | PostgreSQL + SQLAlchemy | Persistent storage, reference numbers |
| Background Jobs | APScheduler | Automated monitoring and uploads |
| XML Processing | lxml | Job feed generation and parsing |
| Authentication | Flask-Login | Secure admin access |
| Email | SendGrid | Notifications and applicant routing |
| File Transfer | Paramiko (SFTP) | Secure XML distribution |

### 3.3 Domain Architecture

JobPulse™ operates across multiple domains to serve different functions:

| Domain | Purpose | Users |
|--------|---------|-------|
| jobpulse.lyntrix.ai | Admin dashboard, settings, monitoring | Internal staff |
| apply.myticas.com | Myticas-branded job application portal | Candidates |
| apply.stsigroup.com | STSI-branded job application portal | Candidates |

---

## 4. Core Capabilities

### 4.1 Dual-Cycle Monitoring System

JobPulse™ implements a sophisticated dual-cycle monitoring architecture:

**5-Minute Monitoring Cycle**
- Real-time tearsheet scanning
- Instant new job detection
- UI visibility updates
- Email notifications for new positions

**30-Minute Upload Cycle**
- Full XML regeneration
- SFTP synchronization
- Reference number preservation
- Cross-board distribution

```
┌─────────────────────────────────────────────────────────────┐
│                  DUAL-CYCLE ARCHITECTURE                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   5-MIN CYCLE                    30-MIN CYCLE               │
│   ───────────                    ────────────               │
│   ┌─────────┐                    ┌─────────┐               │
│   │ Scan    │                    │ Generate│               │
│   │ Tear-   │                    │ Full    │               │
│   │ sheets  │                    │ XML     │               │
│   └────┬────┘                    └────┬────┘               │
│        │                              │                     │
│        ▼                              ▼                     │
│   ┌─────────┐                    ┌─────────┐               │
│   │ Detect  │                    │ Apply   │               │
│   │ Changes │                    │ Ref #s  │               │
│   └────┬────┘                    └────┬────┘               │
│        │                              │                     │
│        ▼                              ▼                     │
│   ┌─────────┐                    ┌─────────┐               │
│   │ Notify  │                    │ SFTP    │               │
│   │ Email   │                    │ Upload  │               │
│   └─────────┘                    └─────────┘               │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 Database-First Reference Number System

The reference number system ensures persistent, consistent job identifiers:

**Architecture:**
- `JobReferenceNumber` table serves as single source of truth
- Reference numbers generated once, never regenerated
- 120-hour refresh cycle for database maintenance
- Fallback to published XML if database unavailable

**Workflow:**
```
1. New Job Detected → Generate Unique Reference Number
2. Store in JobReferenceNumber table
3. All XML generations read from database
4. Reference persists for job lifetime
```

### 4.3 Intelligent Job Classification

JobPulse™ implements keyword-based classification aligned with LinkedIn's official taxonomy:

| Category | Options | Method |
|----------|---------|--------|
| Job Functions | 28 categories | Keyword matching |
| Industries | 20 sectors | Title/description analysis |
| Seniority Levels | 5 levels | Title pattern recognition |

**Performance Characteristics:**
- Classification time: <1 second per job
- No external API dependencies
- Weighted scoring (title: 3x, description: 1x)
- Guaranteed fallback defaults

### 4.4 Job Application Processing

Public-facing application forms with intelligent resume parsing:

**Features:**
- Resume upload (PDF, Word)
- Contact information extraction
- Auto-population of form fields
- Bullhorn job ID integration
- Email routing to apply@myticas.com
- Brand-specific styling

### 4.5 Zero-Job Detection Safeguard

Protection against API failures corrupting job feeds:

**Trigger Condition:** API returns 0 jobs when XML contains ≥5 jobs

**Response Actions:**
1. Block XML update
2. Create timestamped backup
3. Send single alert email
4. Preserve existing job data

---

## 5. Technical Implementation

### 5.1 Database Schema

#### Core Models

```
┌────────────────────────────────────────────────────────────┐
│                    DATABASE SCHEMA                          │
├────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ JobReferenceNumber                                   │  │
│  ├─────────────────────────────────────────────────────┤  │
│  │ id: Integer (PK)                                     │  │
│  │ job_id: String(255) [Unique, Indexed]               │  │
│  │ reference_number: String(50)                         │  │
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

### 5.2 XML Processing Engine

**XML Structure Requirements:**
- Root element: `<source>`
- Required fields: title, company, date, referencenumber
- All fields wrapped in CDATA
- HTML descriptions parsed with lxml

**Generation Process:**
```
1. Pull jobs from Bullhorn tearsheets
2. Apply job classification
3. Load reference numbers from database
4. Generate CDATA-wrapped XML
5. Validate structure
6. Upload to SFTP
```

### 5.3 Bullhorn API Integration

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

## 6. Integration Framework

### 6.1 Bullhorn ATS Integration

**Connection Method:** REST API with OAuth 2.0

**Data Flow:**
```
Bullhorn → JobPulse → XML Feed → Job Boards
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

### 6.2 SendGrid Email Integration

**Use Cases:**
- New job notifications
- Application confirmations
- System alerts
- Production monitoring

**Sender Configuration:**
- From: info@myticas.com
- Reply-To: apply@myticas.com

### 6.3 SFTP Distribution

**Protocol:** SFTP (Secure File Transfer Protocol)
**Port:** 2222
**Frequency:** Every 30 minutes (when enabled)

---

## 7. Security & Compliance

### 7.1 Authentication & Authorization

| Feature | Implementation |
|---------|----------------|
| User Authentication | Flask-Login with session management |
| Password Security | Werkzeug password hashing |
| Session Encryption | Secure session keys |
| Admin Access Control | Role-based permissions |

### 7.2 Data Protection

| Measure | Description |
|---------|-------------|
| HTTPS | All traffic encrypted via TLS |
| Secrets Management | Environment-based secret storage |
| Database Security | Connection pooling with pre-ping validation |
| SFTP | Secure file transfers (not FTP) |

### 7.3 Operational Security

- ProxyFix middleware for proper HTTPS handling
- No secrets exposed in logs or UI
- Automated backup creation before destructive operations
- Zero-job safeguard prevents data corruption

---

## 8. Deployment Architecture

### 8.1 Infrastructure

**Hosting Platform:** Replit Cloud
**Database:** PostgreSQL (Neon-backed)
**CDN/Proxy:** Replit edge network

### 8.2 Environment Configuration

| Environment | Purpose | XML Target |
|-------------|---------|------------|
| Development | Testing, staging | `-dev.xml` |
| Production | Live operations | `.xml` |

### 8.3 Health Monitoring

**Endpoints:**
- `/health` - Overall system health
- `/ready` - Database connectivity
- `/alive` - Application responsiveness
- `/ping` - Ultra-fast availability

**External Monitoring:** UptimeRobot integration

---

## 9. Performance & Reliability

### 9.1 Performance Metrics

| Metric | Target | Actual |
|--------|--------|--------|
| Page Load Time | <2s | 1.2s average |
| XML Generation | <30s | 15-20s |
| Classification | <1s | <100ms |
| Uptime | 99.9% | 99.95% |

### 9.2 Reliability Features

- **Automatic Restart:** Scheduler auto-recovery with timeout protection
- **Database Resilience:** Connection pooling (300s recycle, pre-ping)
- **Backup System:** Timestamped backups before critical operations
- **Error Handling:** Comprehensive logging with user-friendly messages

### 9.3 Incident Management

**November 2025 Incident - Post-Mortem:**
- Root cause: Invalid API field caused 0-job response
- Impact: 68 false "new job" emails sent
- Resolution: Zero-job detection safeguard implemented
- Prevention: Single alert email instead of per-job notifications

---

## 10. Future Roadmap

### 10.1 Near-Term (Q1 2026)

| Initiative | Description | Status |
|------------|-------------|--------|
| Bullhorn One Migration | Transition to new ATS platform | Scheduled Jan 26 |
| Enhanced Analytics | Job trend visualization dashboard | Planned |
| AI Email Parser | Automated applicant routing to ATS | Concept |

### 10.2 Medium-Term (Q2-Q3 2026)

- Multi-tenant support for additional clients
- GraphQL API for third-party integrations
- Real-time WebSocket dashboard updates
- Advanced reporting and analytics

### 10.3 Long-Term Vision

- Full ATS-agnostic platform
- AI-powered job matching
- Predictive analytics for hiring trends
- White-label solution for staffing agencies

---

## 11. Appendices

### 11.1 Technology Stack Summary

| Layer | Technologies |
|-------|--------------|
| Frontend | Bootstrap 5, Font Awesome 6, Vanilla JS |
| Backend | Flask, Python 3.x, Gunicorn |
| Database | PostgreSQL, SQLAlchemy ORM |
| Processing | lxml, APScheduler |
| Integrations | Bullhorn REST API, SendGrid, SFTP |

### 11.2 API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Dashboard |
| `/settings` | GET/POST | Configuration |
| `/ats-monitoring` | GET | Tearsheet status |
| `/refresh-all` | POST | Manual refresh |
| `/health` | GET | Health check |
| `/apply` | GET/POST | Job application |

### 11.3 Glossary

| Term | Definition |
|------|------------|
| ATS | Applicant Tracking System |
| Tearsheet | Bullhorn job collection/list |
| Reference Number | Unique job identifier for tracking |
| SFTP | Secure File Transfer Protocol |
| CDATA | Character Data (XML formatting) |

---

**Document Control**

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | Sept 2025 | Development Team | Initial release |
| 2.0 | Oct 2025 | Development Team | Database-backed references |
| 2.5 | Jan 2026 | Development Team | Bullhorn One preparation |

---

*This document is confidential and intended for investor and stakeholder review only.*

**Contact:** kroots@myticas.com  
**Platform:** jobpulse.lyntrix.ai
