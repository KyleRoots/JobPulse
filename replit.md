# JobPulse™ - AI-Powered Job Feed Automation Platform

## Overview
JobPulse is a Flask-based web application designed to automate XML job feed processing, synchronize job listings with Bullhorn ATS/CRM, and provide AI-powered candidate vetting (Scout Vetting). Its primary purpose is to maintain accurate, real-time job listings, streamline application workflows, and enhance recruitment efficiency through automation. The project aims to become a multi-tenant SaaS platform, revolutionizing recruitment operations.

## User Preferences
Preferred communication style: Simple, everyday language.
Deployment workflow: Always confirm deployment requirements at the end of any changes or updates.
Development Approval Process: Before executing any development task, always provide "stack recommendation" including:
  - Model type (Standard/High-Performance)
  - Extended thinking (Yes/No)
  - Brief rationale for the choice
  - Wait for user approval before proceeding
Source of Truth: GitHub repository (KyleRoots/JobPulse) — main branch.

## System Architecture

### UI/UX Decisions
- **Template Engine**: Jinja2 with Bootstrap 5 (dark theme) for a responsive and modern user interface.
- **Client-side**: Vanilla JavaScript for interactive elements.
- **Icons**: Font Awesome 6.0.
- **Dual-Domain Architecture**: Supports `app.scoutgenius.ai` (main application) and `apply.myticas.com` / `apply.stsigroup.com` (job application forms).

### Technical Implementations
- **Web Framework**: Flask (Python 3.11) utilizing modular route blueprints.
- **Database**: PostgreSQL (Neon-hosted) with SQLAlchemy ORM and Alembic for migrations.
- **Authentication**: Flask-Login for user management, supporting username or email login, with a secure password reset flow.
- **Authorization**: Granular module-based access control (`subscribed_modules` on User model) with route guards.
- **User Management**: Admin interface for user CRUD, module subscriptions, Bullhorn User ID assignment, password management, and account activation/reset emails. Includes "View As" impersonation for admins.
- **Background Processing**: APScheduler manages automated tasks for monitoring and health checks.
- **XML Processing**: Custom `lxml` processor for data handling, reference number generation, and HTML consistency.
- **Email Service**: SendGrid for notifications and delivery logging.
- **AI/LLM Integration**: OpenAI GPT-4o for candidate vetting, job classification, and resume formatting. Claude Opus 4 powers the Product Expert Workbench (dev-only).
- **Embedding Service**: Used for similarity-based pre-filtering in candidate-job matching.
- **Error Tracking**: Sentry SDK integration for optional error monitoring.
- **Testing**: Comprehensive pytest suite.
- **Proxy Support**: `ProxyFix` middleware for reverse proxy environments.

### Feature Specifications
- **Dual-Cycle Monitoring**: 5-minute tearsheet monitoring and 30-minute automated SFTP upload cycles with smart cleanup of ineligible jobs.
- **Real-Time Notifications**: Email alerts for new jobs on monitored tearsheets.
- **Environment Isolation**: Separate development and production configurations.
- **Database-First Reference Numbers**: `JobReferenceNumber` table as the single source of truth.
- **Job Application Forms**: Public forms with multi-brand support, resume parsing (Word/PDF), and Bullhorn integration.
- **Resume HTML Formatting**: Three-layer process: extraction, normalization, and GPT-4o formatting.
- **AI Job Classification**: Classifies jobs based on LinkedIn taxonomy.
- **Zero-Job Detection Safeguard**: Prevents XML corruption from empty API responses.
- **Zero-Touch Deployment**: Environment-aware database seeding.
- **Scout Vetting**: AI-powered candidate screening using GPT-4o with embedding pre-filtering, experience-level classification, location-aware scoring, work authorization/security clearance inference, and configurable global screening prompts.
- **Vetting System Health Monitoring**: Automated checks for Bullhorn, OpenAI, database, and scheduler status.
- **Scout Screening Portal**: Recruiter-facing dashboard displaying AI match results, including scores, qualification status, and AI notes, with per-job threshold and override capabilities.
- **Module Switcher**: UI component for non-admin users subscribed to multiple modules. Pill navigation in top navbar, visible only when user has 2+ modules.
- **Company Admin Role**: A specialized role (`is_company_admin=True`) with `is_any_admin`, `can_view_all_users`, and `effective_role` properties. Gets only their own subscribed modules (not all modules like super-admin). Grantable via admin user settings UI. `effective_role` values: `super_admin`, `company_admin`, `user`.
- **Multi-Company Support**: `company` field on User model (default: `Myticas Consulting`). Company admins see only users within their own company via `get_visible_users()`. Super-admins see all. Impersonation is company-scoped for company admins. Company dropdown in admin user management UI. Company name displayed in sidebar. Supported companies: `Myticas Consulting`, `STSI Group`. Settings user list has pill filter toggle (All / Myticas / STSI) matching the Support Directory UX.
- **Product Expert Workbench (Dev-Only)**: Claude Opus 4-powered chat interface for building custom Bullhorn automations, including built-in automations and execution logging. Hidden in production.

## External Dependencies

- **Python Libraries**: Flask, Flask-Login, Flask-WTF, Flask-SQLAlchemy, lxml, SQLAlchemy, Alembic, APScheduler, gunicorn, SendGrid, OpenAI, tiktoken, PyMuPDF, PyPDF2, python-docx, Paramiko, Requests, httpx, Sentry SDK, bcrypt, BeautifulSoup4.
- **Frontend Libraries**: Bootstrap 5, Font Awesome 6.
- **External Services**: PostgreSQL (Neon-hosted), SendGrid, OpenAI, Bullhorn ATS/CRM, Sentry.
- **AI Models**: OpenAI GPT-4o, Anthropic Claude Opus 4.