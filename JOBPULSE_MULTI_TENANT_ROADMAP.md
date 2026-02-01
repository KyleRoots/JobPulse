# JobPulse™ Multi-Tenant SaaS Roadmap
## Software Design & Requirements Document

**Document Version:** 1.0  
**Last Updated:** February 1, 2026  
**Classification:** Strategic Planning  

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Current State Analysis](#current-state-analysis)
3. [Target State Vision](#target-state-vision)
4. [Gap Analysis](#gap-analysis)
5. [Multi-Tenant Architecture Design](#multi-tenant-architecture-design)
6. [Database Schema Evolution](#database-schema-evolution)
7. [ATS Adapter Framework](#ats-adapter-framework)
8. [Role-Based Access Control](#role-based-access-control)
9. [Implementation Roadmap](#implementation-roadmap)
10. [Infrastructure & Scaling](#infrastructure-scaling)
11. [Risk Assessment & Mitigation](#risk-assessment-mitigation)
12. [Appendices](#appendices)

---

## Executive Summary

### Purpose
This document outlines the strategic transformation of JobPulse™ from a single-tenant XML job feed automation system into a scalable multi-tenant SaaS platform capable of serving multiple staffing and recruiting companies simultaneously.

### Current State
JobPulse™ currently operates as a dedicated solution for Myticas Consulting, providing:
- Automated XML job feed processing with Bullhorn ATS integration
- AI-powered candidate vetting using GPT-4o
- Real-time tearsheet monitoring and SFTP synchronization
- Recruiter email notifications with collaborative threading
- Dual-domain support for job application processing

### Strategic Objective
Transform JobPulse™ into a multi-tenant SaaS platform that can:
- Onboard 50+ customers with isolated data environments
- Support multiple ATS systems (Bullhorn, Greenhouse, Workday, etc.)
- Provide tiered access levels (Super Admin, Customer Admin, Recruiter)
- Scale infrastructure dynamically based on customer growth

### Key Benefits
- **Revenue Scalability:** Recurring SaaS revenue model
- **Operational Efficiency:** Centralized management of multiple customers
- **Competitive Advantage:** Multi-ATS support differentiates from single-vendor solutions
- **Customer Value:** Self-service features reduce support burden

---

## Current State Analysis

### Platform Overview

| Attribute | Current Value |
|-----------|---------------|
| **Architecture** | Single-tenant |
| **Infrastructure** | Reserved VM (2 vCPU / 8 GiB RAM) |
| **Database** | PostgreSQL (Neon) |
| **Primary ATS** | Bullhorn |
| **AI Provider** | OpenAI GPT-4o |
| **Email Service** | SendGrid |
| **Deployment** | Replit Production + Dev |

### Current Feature Set

#### 1. XML Job Feed Management
- Automated reference number generation and preservation
- Real-time Bullhorn tearsheet monitoring (5-minute cycles)
- SFTP upload to job boards (30-minute cycles)
- Smart job classification using keyword dictionaries
- LinkedIn taxonomy compliance

#### 2. AI Candidate Vetting
- Automatic detection of inbound applicants
- Resume parsing and extraction (Word/PDF)
- GPT-4o matching with scoring (0-100%)
- Location-aware matching rules
- Bullhorn note creation with detailed explanations
- Recruiter email notifications with collaborative threading

#### 3. Job Application Processing
- Dual-domain support (myticas.com, stsigroup.com)
- Resume upload with AI formatting
- Bullhorn candidate/submission creation
- Duplicate candidate detection

#### 4. Monitoring & Operations
- Environment health monitoring
- Vetting system health checks
- Email delivery logging
- Activity audit trails

### Current Database Schema

```
┌─────────────────────────────────────────────────────────────────────┐
│                     CURRENT DATABASE (21 TABLES)                    │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  CORE DATA                           CONFIGURATION                  │
│  ├─ job_reference_number             ├─ global_settings             │
│  ├─ bullhorn_monitor                 ├─ vetting_config              │
│  ├─ bullhorn_activity                ├─ email_parsing_config        │
│  ├─ tearsheet_job_history            └─ schedule_config             │
│  └─ recruiter_mapping                                               │
│                                                                     │
│  CANDIDATE VETTING                   OPERATIONS                     │
│  ├─ candidate_vetting_log            ├─ processing_log              │
│  ├─ candidate_job_match              ├─ refresh_log                 │
│  ├─ job_vetting_requirements         ├─ email_delivery_log          │
│  └─ parsed_email                     ├─ scheduler_lock              │
│                                      ├─ environment_status          │
│  AUTHENTICATION                      ├─ environment_alert           │
│  └─ user                             └─ vetting_health_check        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Current Integrations

| Integration | Purpose | Authentication |
|-------------|---------|----------------|
| **Bullhorn REST API** | Job/candidate data sync | OAuth 2.0 |
| **OpenAI API** | Resume analysis, job matching | API Key |
| **SendGrid** | Email notifications | API Key |
| **SFTP** | Job board uploads | Username/Password |

### Current User Model
- Single admin user (Super Admin level access)
- No customer segmentation
- All data globally accessible
- Dev environment for management
- Production environment for visualization

---

## Target State Vision

### Multi-Tenant SaaS Platform

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SUPER ADMIN PORTAL                           │
│                   (Lyntrix/Myticas Operations)                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ • Customer Onboarding/Offboarding                            │  │
│  │ • Global System Configuration                                │  │
│  │ • Cross-Tenant Analytics & Reporting                         │  │
│  │ • Billing & Subscription Management                          │  │
│  │ • ATS Integration Marketplace                                │  │
│  └──────────────────────────────────────────────────────────────┘  │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         ▼                       ▼                       ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   CUSTOMER A    │     │   CUSTOMER B    │     │   CUSTOMER C    │
│   (Bullhorn)    │     │  (Greenhouse)   │     │   (Workday)     │
├─────────────────┤     ├─────────────────┤     ├─────────────────┤
│ Customer Admin  │     │ Customer Admin  │     │ Customer Admin  │
│ ├─ Settings     │     │ ├─ Settings     │     │ ├─ Settings     │
│ ├─ Thresholds   │     │ ├─ Thresholds   │     │ ├─ Thresholds   │
│ └─ Reports      │     │ └─ Reports      │     │ └─ Reports      │
├─────────────────┤     ├─────────────────┤     ├─────────────────┤
│ Recruiters      │     │ Recruiters      │     │ Recruiters      │
│ ├─ View Only    │     │ ├─ View Only    │     │ ├─ View Only    │
│ └─ Notifications│     │ └─ Notifications│     │ └─ Notifications│
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

### Target Capabilities

#### Multi-ATS Support
- **Phase 1:** Bullhorn (current)
- **Phase 2:** Greenhouse, Lever
- **Phase 3:** Workday, iCIMS, ADP, SAP SuccessFactors

#### Tenant Isolation
- Complete data segregation per customer
- Separate API credentials per tenant
- Independent processing queues
- Tenant-specific customizations

#### Self-Service Customer Portal
- Onboarding wizard for new customers
- ATS connection setup
- Threshold and notification configuration
- Activity dashboards and reports

#### Tiered Subscription Model
| Tier | Features | Jobs | Candidates/Month | Price Range |
|------|----------|------|------------------|-------------|
| **Starter** | XML Feed + Monitoring | Up to 50 | 200 | $299/mo |
| **Professional** | + AI Vetting | Up to 200 | 1,000 | $599/mo |
| **Enterprise** | + Multi-ATS + API | Unlimited | Unlimited | Custom |

---

## Gap Analysis

### Technology Gaps

| Current State | Required State | Gap | Effort |
|---------------|----------------|-----|--------|
| Single Bullhorn integration | Multi-ATS adapter pattern | Need adapter abstraction layer | High |
| Single user model | Multi-tenant RBAC | Need tenant + role system | High |
| Shared database | Tenant-isolated data | Add tenant_id to all tables | Medium |
| Single credential set | Per-tenant credentials | Credential vault per tenant | Medium |
| Manual onboarding | Self-service portal | Need onboarding wizard | Medium |
| Dev-only management | Customer portals | Need customer-facing UI | High |

### Operational Gaps

| Current State | Required State | Gap |
|---------------|----------------|-----|
| Single customer | 50+ customers | Batch processing, rate limiting |
| Shared queues | Per-tenant queues | Job queue architecture |
| Single SFTP | Per-customer SFTP | Credential management |
| Admin notifications only | Customer-facing alerts | Alert routing system |

---

## Multi-Tenant Architecture Design

### Tenant Data Model

```python
class Tenant(db.Model):
    """Represents a customer organization"""
    id = db.Column(db.String(36), primary_key=True, default=uuid4)
    name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(100), unique=True, nullable=False)  # URL-safe identifier
    
    # Subscription & Billing
    subscription_tier = db.Column(db.String(50), default='starter')  # starter, professional, enterprise
    subscription_status = db.Column(db.String(50), default='trial')  # trial, active, suspended, cancelled
    trial_ends_at = db.Column(db.DateTime, nullable=True)
    
    # Limits (based on subscription)
    max_jobs = db.Column(db.Integer, default=50)
    max_candidates_per_month = db.Column(db.Integer, default=200)
    
    # ATS Configuration
    ats_type = db.Column(db.String(50), nullable=False)  # bullhorn, greenhouse, workday, etc.
    ats_credentials_encrypted = db.Column(db.Text, nullable=True)  # Encrypted JSON
    
    # Branding
    company_logo_url = db.Column(db.String(500), nullable=True)
    primary_color = db.Column(db.String(7), default='#2980b9')
    
    # Configuration
    timezone = db.Column(db.String(50), default='America/New_York')
    notification_email = db.Column(db.String(255), nullable=True)
    
    # Status
    is_active = db.Column(db.Boolean, default=True)
    onboarded_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    users = db.relationship('TenantUser', backref='tenant', lazy='dynamic')
    jobs = db.relationship('TenantJob', backref='tenant', lazy='dynamic')


class TenantUser(db.Model):
    """User belonging to a tenant with specific role"""
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.String(36), db.ForeignKey('tenant.id'), nullable=False)
    
    # Authentication
    email = db.Column(db.String(255), nullable=False)
    password_hash = db.Column(db.String(256), nullable=True)  # Null for SSO users
    
    # Profile
    first_name = db.Column(db.String(100), nullable=True)
    last_name = db.Column(db.String(100), nullable=True)
    
    # Role & Permissions
    role = db.Column(db.String(50), nullable=False)  # customer_admin, recruiter
    
    # Status
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Unique constraint: email unique per tenant
    __table_args__ = (
        db.UniqueConstraint('tenant_id', 'email', name='unique_tenant_email'),
    )


class SuperAdmin(db.Model):
    """Platform administrators (Lyntrix staff)"""
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    name = db.Column(db.String(255), nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
```

### Tenant Context Middleware

```python
from flask import g, request
from functools import wraps

def tenant_context_middleware():
    """Middleware to set current tenant context from subdomain or header"""
    
    # Option 1: Subdomain-based (customer.jobpulse.ai)
    host = request.host
    if '.jobpulse.ai' in host:
        tenant_slug = host.split('.')[0]
        g.current_tenant = Tenant.query.filter_by(slug=tenant_slug, is_active=True).first()
    
    # Option 2: Header-based (X-Tenant-ID)
    elif 'X-Tenant-ID' in request.headers:
        tenant_id = request.headers.get('X-Tenant-ID')
        g.current_tenant = Tenant.query.get(tenant_id)
    
    else:
        g.current_tenant = None


def requires_tenant(f):
    """Decorator to ensure tenant context is available"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not g.get('current_tenant'):
            abort(404, description="Tenant not found")
        return f(*args, **kwargs)
    return decorated_function


def tenant_scoped_query(model):
    """Return query scoped to current tenant"""
    return model.query.filter_by(tenant_id=g.current_tenant.id)
```

---

## Database Schema Evolution

### Phase 1: Add Tenant Column (Backward Compatible)

```sql
-- Step 1: Create tenant table
CREATE TABLE tenant (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    subscription_tier VARCHAR(50) DEFAULT 'starter',
    ats_type VARCHAR(50) NOT NULL DEFAULT 'bullhorn',
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Step 2: Create default tenant for existing data
INSERT INTO tenant (id, name, slug, ats_type) 
VALUES ('default-myticas', 'Myticas Consulting', 'myticas', 'bullhorn');

-- Step 3: Add tenant_id to all data tables (nullable initially)
ALTER TABLE job_reference_number ADD COLUMN tenant_id VARCHAR(36);
ALTER TABLE bullhorn_monitor ADD COLUMN tenant_id VARCHAR(36);
ALTER TABLE candidate_vetting_log ADD COLUMN tenant_id VARCHAR(36);
ALTER TABLE parsed_email ADD COLUMN tenant_id VARCHAR(36);
-- ... (all other tables)

-- Step 4: Migrate existing data to default tenant
UPDATE job_reference_number SET tenant_id = 'default-myticas' WHERE tenant_id IS NULL;
UPDATE bullhorn_monitor SET tenant_id = 'default-myticas' WHERE tenant_id IS NULL;
-- ... (all other tables)

-- Step 5: Add foreign key constraints
ALTER TABLE job_reference_number 
    ADD CONSTRAINT fk_job_ref_tenant 
    FOREIGN KEY (tenant_id) REFERENCES tenant(id);

-- Step 6: Make tenant_id NOT NULL after migration
ALTER TABLE job_reference_number ALTER COLUMN tenant_id SET NOT NULL;

-- Step 7: Add indexes for tenant-scoped queries
CREATE INDEX idx_job_ref_tenant ON job_reference_number(tenant_id);
CREATE INDEX idx_monitor_tenant ON bullhorn_monitor(tenant_id);
```

### Multi-Tenant Table Structure

```
┌─────────────────────────────────────────────────────────────────────┐
│                  MULTI-TENANT DATABASE SCHEMA                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  PLATFORM TABLES (No tenant_id)      TENANT-SCOPED TABLES          │
│  ├─ tenant                           ├─ job_reference_number        │
│  ├─ super_admin                      ├─ bullhorn_monitor            │
│  ├─ tenant_user                      ├─ bullhorn_activity           │
│  ├─ subscription_plan                ├─ candidate_vetting_log       │
│  └─ platform_settings                ├─ candidate_job_match         │
│                                      ├─ job_vetting_requirements    │
│                                      ├─ parsed_email                │
│  ATS ADAPTERS                        ├─ recruiter_mapping           │
│  ├─ ats_adapter_config               ├─ tearsheet_job_history       │
│  └─ ats_credentials (encrypted)      ├─ vetting_config              │
│                                      ├─ email_delivery_log          │
│                                      └─ processing_log              │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## ATS Adapter Framework

### Adapter Interface

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Dict, Any

@dataclass
class Job:
    """Universal job representation"""
    id: str
    title: str
    description: str
    city: Optional[str]
    state: Optional[str]
    country: Optional[str]
    remote_type: Optional[str]  # remote, hybrid, onsite
    owner_name: Optional[str]
    owner_email: Optional[str]
    posted_date: Optional[datetime]
    status: str
    raw_data: Dict[str, Any]  # Original ATS-specific data


@dataclass
class Candidate:
    """Universal candidate representation"""
    id: str
    first_name: str
    last_name: str
    email: str
    phone: Optional[str]
    resume_text: Optional[str]
    resume_file_id: Optional[str]
    raw_data: Dict[str, Any]


class ATSAdapter(ABC):
    """Abstract base class for ATS integrations"""
    
    @abstractmethod
    def connect(self, credentials: Dict[str, str]) -> bool:
        """Establish connection to ATS"""
        pass
    
    @abstractmethod
    def get_jobs(self, filters: Dict[str, Any] = None) -> List[Job]:
        """Retrieve jobs from ATS"""
        pass
    
    @abstractmethod
    def get_job_by_id(self, job_id: str) -> Optional[Job]:
        """Get single job by ID"""
        pass
    
    @abstractmethod
    def get_candidates_for_job(self, job_id: str) -> List[Candidate]:
        """Get candidates who applied to a job"""
        pass
    
    @abstractmethod
    def create_note(self, entity_type: str, entity_id: str, note_content: str) -> bool:
        """Create a note on candidate/job"""
        pass
    
    @abstractmethod
    def get_tearsheets(self) -> List[Dict[str, Any]]:
        """Get available tearsheets/hotlists"""
        pass
    
    @abstractmethod
    def get_tearsheet_jobs(self, tearsheet_id: str) -> List[Job]:
        """Get jobs from a specific tearsheet"""
        pass


class BullhornAdapter(ATSAdapter):
    """Bullhorn-specific implementation"""
    
    def __init__(self):
        self.rest_token = None
        self.base_url = None
    
    def connect(self, credentials: Dict[str, str]) -> bool:
        # Existing Bullhorn OAuth implementation
        # Uses credentials['client_id'], credentials['client_secret'], 
        # credentials['username'], credentials['password']
        ...
    
    def get_jobs(self, filters: Dict[str, Any] = None) -> List[Job]:
        # Convert Bullhorn JobOrder to universal Job
        ...


class GreenhouseAdapter(ATSAdapter):
    """Greenhouse-specific implementation (future)"""
    
    def connect(self, credentials: Dict[str, str]) -> bool:
        # Greenhouse uses API token authentication
        ...


class WorkdayAdapter(ATSAdapter):
    """Workday-specific implementation (future)"""
    
    def connect(self, credentials: Dict[str, str]) -> bool:
        # Workday uses OAuth 2.0
        ...


# Adapter Factory
def get_ats_adapter(ats_type: str) -> ATSAdapter:
    """Factory function to get appropriate adapter"""
    adapters = {
        'bullhorn': BullhornAdapter,
        'greenhouse': GreenhouseAdapter,
        'workday': WorkdayAdapter,
    }
    
    adapter_class = adapters.get(ats_type.lower())
    if not adapter_class:
        raise ValueError(f"Unsupported ATS type: {ats_type}")
    
    return adapter_class()
```

### ATS-Specific Mapping

| Feature | Bullhorn | Greenhouse | Workday |
|---------|----------|------------|---------|
| **Jobs** | JobOrder | Job | Job Requisition |
| **Candidates** | Candidate | Candidate | Prospect |
| **Applications** | JobSubmission | Application | Job Application |
| **Notes** | Note | Note | Comment |
| **Hotlists** | Tearsheet | Saved Search | Talent Pool |
| **Auth** | OAuth 2.0 | API Token | OAuth 2.0 |

---

## Role-Based Access Control

### Role Hierarchy

```
┌─────────────────────────────────────────────────────────────────────┐
│                         SUPER ADMIN                                 │
│                    (Platform Operators)                             │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │ • Full platform access                                        │ │
│  │ • Create/manage tenants                                       │ │
│  │ • View all tenant data                                        │ │
│  │ • System configuration                                        │ │
│  │ • Billing & subscriptions                                     │ │
│  │ • ATS adapter management                                      │ │
│  └───────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────┤
│                       CUSTOMER ADMIN                                │
│                    (Company Administrators)                         │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │ • Own tenant data only                                        │ │
│  │ • Manage tenant users                                         │ │
│  │ • Configure vetting thresholds                                │ │
│  │ • Set notification preferences                                │ │
│  │ • View all activity within tenant                             │ │
│  │ • Manage recruiter mappings                                   │ │
│  │ • Enable/disable features                                     │ │
│  └───────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────┤
│                         RECRUITER                                   │
│                      (End Users)                                    │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │ • View-only access                                            │ │
│  │ • See candidates matched to their jobs                        │ │
│  │ • Receive notifications                                       │ │
│  │ • View match scores and explanations                          │ │
│  │ • Cannot modify settings                                      │ │
│  └───────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

### Permission Matrix

| Permission | Super Admin | Customer Admin | Recruiter |
|------------|:-----------:|:--------------:|:---------:|
| View all tenants | ✅ | ❌ | ❌ |
| Create tenant | ✅ | ❌ | ❌ |
| Delete tenant | ✅ | ❌ | ❌ |
| View tenant data | ✅ (all) | ✅ (own) | ✅ (own) |
| Manage tenant users | ✅ | ✅ | ❌ |
| Configure ATS | ✅ | ✅ | ❌ |
| Set vetting threshold | ✅ | ✅ | ❌ |
| Enable/disable vetting | ✅ | ✅ | ❌ |
| View candidates | ✅ | ✅ | ✅ (assigned) |
| View match scores | ✅ | ✅ | ✅ |
| Receive notifications | ✅ | ✅ | ✅ |
| Manage billing | ✅ | ❌ | ❌ |

### Permission Implementation

```python
from enum import Enum
from functools import wraps

class Permission(Enum):
    # Tenant Management
    CREATE_TENANT = "create_tenant"
    DELETE_TENANT = "delete_tenant"
    VIEW_ALL_TENANTS = "view_all_tenants"
    
    # User Management
    MANAGE_USERS = "manage_users"
    
    # Settings
    CONFIGURE_ATS = "configure_ats"
    CONFIGURE_VETTING = "configure_vetting"
    MANAGE_NOTIFICATIONS = "manage_notifications"
    
    # Data Access
    VIEW_CANDIDATES = "view_candidates"
    VIEW_MATCHES = "view_matches"
    VIEW_ACTIVITY = "view_activity"
    
    # Billing
    MANAGE_BILLING = "manage_billing"


ROLE_PERMISSIONS = {
    'super_admin': [p for p in Permission],  # All permissions
    
    'customer_admin': [
        Permission.MANAGE_USERS,
        Permission.CONFIGURE_ATS,
        Permission.CONFIGURE_VETTING,
        Permission.MANAGE_NOTIFICATIONS,
        Permission.VIEW_CANDIDATES,
        Permission.VIEW_MATCHES,
        Permission.VIEW_ACTIVITY,
    ],
    
    'recruiter': [
        Permission.VIEW_CANDIDATES,
        Permission.VIEW_MATCHES,
        Permission.VIEW_ACTIVITY,
    ],
}


def requires_permission(permission: Permission):
    """Decorator to check user permission"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            user = current_user
            role = getattr(user, 'role', None)
            
            allowed_permissions = ROLE_PERMISSIONS.get(role, [])
            if permission not in allowed_permissions:
                abort(403, description="Insufficient permissions")
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator
```

---

## Implementation Roadmap

### Phase 1: Foundation (4-6 weeks)
**Goal:** Add tenant isolation while maintaining current functionality

#### Milestone 1.1: Database Migration (Week 1-2)
- [ ] Create `tenant` table
- [ ] Create default tenant for Myticas data
- [ ] Add `tenant_id` column to all data tables
- [ ] Migrate existing data to default tenant
- [ ] Add foreign key constraints and indexes
- [ ] Update all queries to be tenant-scoped

#### Milestone 1.2: Tenant Context (Week 2-3)
- [ ] Implement tenant context middleware
- [ ] Add tenant_id to all model operations
- [ ] Create tenant-scoped query helpers
- [ ] Update existing routes to use tenant context
- [ ] Add tenant isolation tests

#### Milestone 1.3: User Model Update (Week 3-4)
- [ ] Create `SuperAdmin` and `TenantUser` models
- [ ] Migrate existing admin user
- [ ] Implement role-based access control
- [ ] Update authentication to support both user types
- [ ] Create login routing (super admin vs tenant user)

#### Milestone 1.4: Super Admin Dashboard (Week 4-6)
- [ ] Build tenant management UI
- [ ] Create tenant onboarding form
- [ ] Implement tenant CRUD operations
- [ ] Add cross-tenant analytics view
- [ ] Build tenant health monitoring

**Deliverables:**
- Multi-tenant database with complete data isolation
- Super Admin portal for tenant management
- Backward compatibility with existing Myticas workflow

---

### Phase 2: Customer Portal (4-6 weeks)
**Goal:** Enable customer self-service and recruiter access

#### Milestone 2.1: Customer Admin Portal (Week 1-2)
- [ ] Build customer-facing dashboard
- [ ] Create settings management UI
- [ ] Implement vetting configuration
- [ ] Add recruiter mapping management
- [ ] Build activity logs view

#### Milestone 2.2: Recruiter Portal (Week 2-3)
- [ ] Create recruiter-specific dashboard
- [ ] Build candidate match viewer
- [ ] Implement notification preferences
- [ ] Add job-specific views
- [ ] Create recruiter mobile-friendly views

#### Milestone 2.3: User Management (Week 3-4)
- [ ] Build user invitation system
- [ ] Create user role management
- [ ] Implement password reset flow
- [ ] Add user activity logging
- [ ] Build team overview page

#### Milestone 2.4: Customer Onboarding Wizard (Week 4-6)
- [ ] Create step-by-step onboarding flow
- [ ] Build ATS connection setup wizard
- [ ] Implement tearsheet selection UI
- [ ] Add threshold configuration steps
- [ ] Create onboarding completion verification

**Deliverables:**
- Customer Admin portal with full settings control
- Recruiter view-only portal
- Self-service onboarding for new customers

---

### Phase 3: Multi-ATS Support (6-8 weeks)
**Goal:** Abstract ATS integration and add Greenhouse support

#### Milestone 3.1: Adapter Framework (Week 1-2)
- [ ] Create abstract ATSAdapter interface
- [ ] Define universal data models (Job, Candidate, etc.)
- [ ] Build adapter factory
- [ ] Implement adapter credential management
- [ ] Create adapter testing framework

#### Milestone 3.2: Bullhorn Refactor (Week 2-4)
- [ ] Refactor existing Bullhorn code into adapter
- [ ] Map Bullhorn entities to universal models
- [ ] Update all Bullhorn-specific code to use adapter
- [ ] Maintain backward compatibility
- [ ] Add comprehensive tests

#### Milestone 3.3: Greenhouse Adapter (Week 4-6)
- [ ] Implement Greenhouse authentication
- [ ] Map Greenhouse entities to universal models
- [ ] Build job synchronization
- [ ] Implement candidate retrieval
- [ ] Add note creation support

#### Milestone 3.4: ATS Selection UI (Week 6-8)
- [ ] Build ATS marketplace page
- [ ] Create ATS-specific setup wizards
- [ ] Implement credential encryption
- [ ] Add connection health monitoring
- [ ] Build ATS switching capability

**Deliverables:**
- Abstract ATS adapter framework
- Bullhorn adapter (refactored)
- Greenhouse adapter (new)
- ATS selection and configuration UI

---

### Phase 4: Scale & Optimize (4-6 weeks)
**Goal:** Prepare for 50+ customers with performance optimization

#### Milestone 4.1: Queue Architecture (Week 1-2)
- [ ] Implement per-tenant job queues
- [ ] Add rate limiting per tenant
- [ ] Build queue monitoring dashboard
- [ ] Implement priority queuing
- [ ] Add queue health alerts

#### Milestone 4.2: Performance Optimization (Week 2-4)
- [ ] Optimize database queries for scale
- [ ] Implement query caching (Redis)
- [ ] Add connection pooling
- [ ] Optimize AI API calls (batching)
- [ ] Implement database read replicas

#### Milestone 4.3: Autoscaling (Week 4-5)
- [ ] Configure Replit Autoscale deployment
- [ ] Implement horizontal scaling
- [ ] Add load balancing
- [ ] Create scaling policies
- [ ] Build scaling dashboard

#### Milestone 4.4: Monitoring & Alerting (Week 5-6)
- [ ] Build tenant-level health monitoring
- [ ] Create SLA dashboards
- [ ] Implement proactive alerting
- [ ] Add usage analytics
- [ ] Build capacity planning tools

**Deliverables:**
- Per-tenant processing queues
- Performance optimizations for 50+ tenants
- Autoscaling configuration
- Comprehensive monitoring

---

## Infrastructure & Scaling

### Current Infrastructure

| Component | Current | Capacity |
|-----------|---------|----------|
| **Compute** | Reserved VM (2 vCPU / 8 GiB) | 1 customer comfortably |
| **Database** | Neon PostgreSQL (shared) | ~100K rows |
| **API Calls** | Bullhorn, OpenAI, SendGrid | ~1K/day |

### Scaling Recommendations

#### 5 Customers (Phase 1)
```
┌─────────────────────────────────────────────────────────────────────┐
│                    5-CUSTOMER INFRASTRUCTURE                        │
├─────────────────────────────────────────────────────────────────────┤
│  COMPUTE: Reserved VM (4 vCPU / 16 GiB RAM)                        │
│  DATABASE: Neon PostgreSQL (dedicated pool)                        │
│  CACHE: None required                                              │
│  COST: ~$50-100/month                                              │
│                                                                     │
│  Processing: Sequential (manageable with queue delays)             │
│  API Limits: Watch OpenAI rate limits                              │
└─────────────────────────────────────────────────────────────────────┘
```

#### 20 Customers (Phase 2-3)
```
┌─────────────────────────────────────────────────────────────────────┐
│                   20-CUSTOMER INFRASTRUCTURE                        │
├─────────────────────────────────────────────────────────────────────┤
│  COMPUTE: Reserved VM (8 vCPU / 32 GiB RAM)                        │
│           OR Autoscale (2-4 instances)                             │
│  DATABASE: Neon PostgreSQL (dedicated, connection pooling)         │
│  CACHE: Redis for session/query caching                            │
│  QUEUE: Background worker separation                               │
│  COST: ~$200-400/month                                             │
│                                                                     │
│  Processing: Parallel with tenant queues                           │
│  API Limits: Need OpenAI rate limit management                     │
└─────────────────────────────────────────────────────────────────────┘
```

#### 50+ Customers (Phase 4)
```
┌─────────────────────────────────────────────────────────────────────┐
│                   50+ CUSTOMER INFRASTRUCTURE                       │
├─────────────────────────────────────────────────────────────────────┤
│  COMPUTE: Autoscale Deployment                                     │
│           - Web: 2-8 instances (dynamic)                           │
│           - Workers: 2-4 dedicated instances                       │
│  DATABASE: Neon PostgreSQL Pro (read replicas)                     │
│  CACHE: Redis Cluster                                              │
│  QUEUE: Dedicated job queue service                                │
│  CDN: Static asset delivery                                        │
│  COST: ~$500-1000/month                                            │
│                                                                     │
│  Processing: Distributed with priority scheduling                  │
│  API Limits: Need enterprise OpenAI account                        │
└─────────────────────────────────────────────────────────────────────┘
```

### API Rate Limit Considerations

| Service | Current Limit | 50-Customer Need | Solution |
|---------|---------------|------------------|----------|
| **OpenAI** | 10K TPM | ~500K TPM | Enterprise tier or queue throttling |
| **Bullhorn** | ~100 req/min | ~5K req/min | Per-tenant rate limiting |
| **SendGrid** | 100 emails/day (free) | ~5K emails/day | Pro tier ($15/mo) |

---

## Risk Assessment & Mitigation

### Technical Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Database migration data loss | Low | Critical | Backup + staged rollout |
| ATS adapter complexity | Medium | High | Start with Bullhorn only |
| Performance degradation | Medium | High | Load testing before scale |
| API rate limit exceeded | Medium | Medium | Queue-based throttling |

### Business Risks

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Customer data breach | Low | Critical | Encryption + audit logging |
| Feature scope creep | High | Medium | Strict phase gating |
| Delayed delivery | Medium | Medium | Conservative estimates |
| Customer churn | Medium | High | Gradual rollout + feedback |

### Contingency Plans

1. **Migration Failure:** Maintain ability to run in single-tenant mode indefinitely
2. **Performance Issues:** Reserved VM fallback with dedicated customer instances
3. **ATS Integration Delays:** Bullhorn-only launch, add ATS incrementally

---

## Appendices

### Appendix A: Current Table Inventory

| Table | Records (Est.) | Tenant-Scoped |
|-------|----------------|:-------------:|
| job_reference_number | ~500 | ✅ |
| bullhorn_monitor | 6 | ✅ |
| bullhorn_activity | ~10,000 | ✅ |
| candidate_vetting_log | ~250 | ✅ |
| candidate_job_match | ~3,000 | ✅ |
| job_vetting_requirements | ~60 | ✅ |
| parsed_email | ~500 | ✅ |
| recruiter_mapping | 38 | ✅ |
| vetting_config | 6 | ✅ |
| global_settings | 10 | ❌ (platform) |
| user | 1 | → SuperAdmin |

### Appendix B: API Endpoints (Future)

```
# Platform APIs (Super Admin)
GET    /api/v1/tenants
POST   /api/v1/tenants
GET    /api/v1/tenants/{id}
PATCH  /api/v1/tenants/{id}
DELETE /api/v1/tenants/{id}
GET    /api/v1/tenants/{id}/stats

# Tenant APIs (Customer Admin/Recruiter)
GET    /api/v1/jobs
GET    /api/v1/jobs/{id}
GET    /api/v1/candidates
GET    /api/v1/candidates/{id}
GET    /api/v1/candidates/{id}/matches
GET    /api/v1/activity
PATCH  /api/v1/settings/vetting
GET    /api/v1/stats
```

### Appendix C: Environment Variables (Future)

```bash
# Platform Configuration
JOBPULSE_ENV=production
DATABASE_URL=postgresql://...
REDIS_URL=redis://...

# Encryption
CREDENTIAL_ENCRYPTION_KEY=...

# Super Admin
SUPER_ADMIN_EMAIL=kroots@myticas.com
SUPER_ADMIN_PASSWORD_HASH=...

# API Keys (Platform-level)
OPENAI_API_KEY=...
SENDGRID_API_KEY=...

# Feature Flags
ENABLE_MULTI_TENANT=true
ENABLE_GREENHOUSE_ADAPTER=false
ENABLE_WORKDAY_ADAPTER=false
```

### Appendix D: Glossary

| Term | Definition |
|------|------------|
| **Tenant** | A customer organization using JobPulse |
| **ATS** | Applicant Tracking System (Bullhorn, Greenhouse, etc.) |
| **Vetting** | AI-powered candidate-job matching process |
| **Tearsheet** | Bullhorn hotlist of active job orders |
| **Match Score** | 0-100% compatibility rating from AI analysis |
| **Super Admin** | Platform operator (Lyntrix staff) |
| **Customer Admin** | Customer's internal administrator |
| **Recruiter** | End user who receives candidate matches |

---

## Document Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | Feb 1, 2026 | AI Assistant | Initial document creation |

---

*This document is confidential and intended for internal planning purposes.*
