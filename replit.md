# JobPulse™ - AI-Powered Job Feed Automation Platform

## Overview
JobPulse is a comprehensive Flask-based web application that automates XML job feed processing, synchronizes job listings with Bullhorn ATS/CRM, and provides AI-powered candidate vetting (Scout Vetting). The system maintains accurate, real-time job listings, streamlines application workflows, and enhances recruitment efficiency through automation. The project is the foundation for a planned multi-tenant SaaS platform.

## User Preferences
Preferred communication style: Simple, everyday language.
Deployment workflow: Always confirm deployment requirements at the end of any changes or updates.
Development Approval Process: Before executing any development task, always provide "stack recommendation" including:
  - Model type (Standard/High-Performance)
  - Extended thinking (Yes/No)
  - Brief rationale for the choice
  - Wait for user approval before proceeding
Source of Truth: GitHub repository (KyleRoots/JobPulse) — main branch.

## Project Structure

```
JobPulse/
├── app.py                           # Main Flask application with route registration
├── main.py                          # Entry point with health checks and error handling
├── models.py                        # SQLAlchemy database models (21+ tables)
├── seed_database.py                 # Production-ready database seeding
├── sentry_config.py                 # Sentry error tracking configuration
├── alembic/                         # Database migrations (Alembic)
│   ├── alembic.ini
│   └── versions/
├── config/                          # Configuration files
│   ├── __init__.py
│   └── global_screening_prompt.txt  # AI screening prompt rules
├── routes/                          # Flask route blueprints (modular)
│   ├── __init__.py
│   ├── auth.py                      # Authentication routes
│   ├── dashboard.py                 # Dashboard routes
│   ├── health.py                    # Health check endpoints
│   ├── scheduler.py                 # Scheduler management routes
│   ├── settings.py                  # Settings management
│   ├── triggers.py                  # API trigger endpoints
│   ├── vetting.py                   # Vetting/screening routes
│   ├── ats_integration.py           # ATS integration routes
│   └── automations.py               # Product Expert Workbench routes (dev-only)
├── utils/                           # Utility functions
│   ├── __init__.py
│   └── field_mappers.py             # Data field mapping utilities
├── scripts/                         # Management and testing scripts
├── tests/                           # Test suite (pytest, 40+ test files)
│   ├── conftest.py
│   ├── test_scout_vetting.py
│   ├── test_routes.py
│   └── ...
├── templates/                       # Jinja2 HTML templates (25 files)
├── static/                          # CSS, JS, images
│   ├── css/
│   ├── js/
│   └── images/
├── # Core Services
├── bullhorn_service.py              # Bullhorn ATS API integration (OAuth 2.0)
├── email_service.py                 # SendGrid email service
├── email_inbound_service.py         # Inbound email parsing
├── candidate_vetting_service.py     # AI candidate vetting engine
├── scout_vetting_service.py         # Scout Vetting Module v2
├── check_vetting_notes.py           # Vetting notes verification
├── embedding_service.py             # Embedding-based similarity filtering
├── embedding_digest_service.py      # Embedding digest emails
├── incremental_monitoring_service.py # 5-min tearsheet monitoring
├── job_application_service.py       # Job application processing
├── job_classification_service.py    # AI job classification
├── resume_parser.py                 # 3-layer resume parsing
├── xml_integration_service.py       # XML building/parsing
├── simplified_xml_generator.py      # Clean XML generation
├── xml_processor.py                 # XML processing utilities
├── xml_change_monitor.py            # XML change detection
├── xml_safeguards.py                # Zero-job safeguards
├── xml_duplicate_prevention.py      # Duplicate job prevention
├── xml_field_sync_service.py        # XML field synchronization
├── ftp_service.py                   # SFTP upload service
├── log_monitoring_service.py        # Log monitoring
├── tearsheet_config.py              # Tearsheet configuration
├── timezone_utils.py                # Eastern Time utilities
├── lightweight_reference_refresh.py # Reference number refresh
├── comprehensive_monitoring_service.py # Full monitoring service
├── automation_service.py            # Product Expert Workbench Claude Opus 4 service
├── salesrep_sync_service.py         # Sales Rep display name sync (30-min cycle)
├── # Documentation
├── JOBPULSE.md                      # Main project documentation
├── JOBPULSE_MULTI_TENANT_ROADMAP.md # Multi-tenant SaaS roadmap
├── JOBPULSE_TECHNICAL_DOCUMENTATION.md # Detailed technical docs
├── RECRUITER_MAPPINGS.md            # Recruiter LinkedIn tag mappings
├── design_guidelines.md             # UI/UX design guidelines
└── xml_backups/                     # XML backup files
```

## System Architecture

### UI/UX Decisions
- **Template Engine**: Jinja2 with Bootstrap 5 (dark theme) for a responsive and modern user interface.
- **Client-side**: Vanilla JavaScript for interactive elements.
- **Icons**: Font Awesome 6.0.
- **Dual-Domain Architecture**: Supports distinct functionalities on `app.scoutgenius.ai` (main app) and `apply.myticas.com` / `apply.stsigroup.com` (job application forms).

### Technical Implementations
- **Web Framework**: Flask (Python 3.11) with modular route blueprints.
- **Database**: PostgreSQL (Neon-hosted) with SQLAlchemy ORM and Alembic migrations.
- **Authentication**: Flask-Login for secure user management and session persistence.
- **Background Processing**: APScheduler manages automated tasks (5-min monitoring, health checks, environment monitoring).
- **XML Processing**: Custom `lxml` processor for data handling, reference number generation, and HTML consistency within XML.
- **Email Service**: SendGrid for notifications and delivery logging.
- **AI/LLM**: OpenAI GPT-4o for candidate vetting, job classification, and resume formatting.
- **Embedding Service**: Similarity-based pre-filtering for candidate-job matching.
- **Error Tracking**: Sentry SDK integration (optional, via SENTRY_DSN).
- **Testing**: pytest with 40+ test files covering routes, services, and vetting logic.
- **Proxy Support**: ProxyFix middleware for reverse proxy environments.

### Feature Specifications
- **Dual-Cycle Monitoring**: 5-minute tearsheet monitoring for UI visibility and 30-minute automated SFTP upload cycles.
- **Smart Tearsheet Auto-Cleanup**: Automatically removes ineligible (closed, blocked status) jobs from tearsheets during monitoring.
- **Real-Time Email Notifications**: Instant email alerts for new jobs added to monitored tearsheets.
- **Environment Isolation**: Separate development and production environments with distinct configurations.
- **Database-First Reference Numbers**: `JobReferenceNumber` table is the single source of truth.
- **Job Application Forms**: Public-facing forms with resume parsing (Word/PDF), Bullhorn integration, dual branding (Myticas/STSI).
- **Resume HTML Formatting**: Three-layer approach: PyMuPDF extraction → deterministic normalization → GPT-4o HTML formatting.
- **AI Job Classification**: LinkedIn taxonomy classification (28 functions, 20 industries, 5 seniority levels).
- **Zero-Job Detection Safeguard**: Prevents XML corruption from empty API responses.
- **Zero-Touch Production Deployment**: Environment-aware database seeding from environment secrets.
- **Scout Vetting (AI Candidate Screening)**: Automated candidate-job matching using GPT-4o with embedding pre-filtering, experience-level classification with 3-gate floor, location-aware scoring, work authorization/security clearance inference rules, and configurable global screening prompts.
- **Vetting System Health Monitoring**: Automated health checks monitoring Bullhorn, OpenAI, database, and scheduler status.
- **Product Expert Workbench** (Dev-Only): Claude Opus 4-powered chat interface for building custom Bullhorn automations via natural language. Supports conversation history, automation task tracking, execution logging, and Bullhorn API operations (search, create, update candidates/jobs/notes). Completely hidden in production — routes return 404 and sidebar item is invisible. Database models: `AutomationTask`, `AutomationLog`, `AutomationChat`.

## External Dependencies

### Python Libraries (Key)
- Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy
- lxml, SQLAlchemy, Alembic
- APScheduler, gunicorn
- SendGrid, OpenAI, tiktoken
- PyMuPDF, PyPDF2, python-docx
- Paramiko (SFTP), Requests, httpx
- Sentry SDK, bcrypt, BeautifulSoup4
- pytest, pytest-flask, pytest-cov

### Frontend Libraries
- Bootstrap 5 (dark theme)
- Font Awesome 6

## Environment Variables
- `DATABASE_URL` - PostgreSQL connection string
- `SESSION_SECRET` - Flask session encryption
- `SENDGRID_API_KEY` - Email service
- `OPENAI_API_KEY` - AI features
- `BULLHORN_PASSWORD` - Bullhorn OAuth
- `OAUTH_REDIRECT_BASE_URL` - OAuth callback base URL
- `ANTHROPIC_API_KEY` - Claude Opus 4 for Product Expert Workbench
- `SENTRY_DSN` - (Optional) Sentry error tracking

## Strategic Planning

### Multi-Tenant SaaS Roadmap
See `JOBPULSE_MULTI_TENANT_ROADMAP.md` for comprehensive planning covering:
- Multi-ATS adapter framework (Bullhorn, Greenhouse, Workday)
- Role-based access control (Super Admin, Customer Admin, Recruiter)
- 4-phase implementation roadmap (18-26 weeks)
- Infrastructure scaling and database schema evolution
