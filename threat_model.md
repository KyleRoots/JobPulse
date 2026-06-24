# Threat Model

## Project Overview

Scout Genius is a public Flask application for recruitment operations. It exposes public job-application and inbound-email entry points, authenticated recruiter/admin workflows, an internal support system, background schedulers, and integrations with PostgreSQL, Bullhorn, SendGrid, OpenAI, Microsoft Entra ID, and OneDrive.

Production scope for this scan is limited to behavior reachable when the app runs with `NODE_ENV`/environment set to production on Replit-hosted public domains. Mockup sandbox flows and routes explicitly guarded out of production are out of scope unless production reachability is demonstrated.

## Assets

- **Recruiter and admin accounts** — authenticated users can access recruiter workflows, Bullhorn-connected operations, support tooling, and large amounts of candidate data.
- **Candidate PII** — names, emails, phone numbers, resumes, job applications, vetting conversations, and fraud-analysis signals.
- **Support-system data** — internal contact directory entries, support tickets, ticket conversations, attachments, and approval messages.
- **Bullhorn and email-side effects** — candidate creation/updates, submissions, notes, outbound support mail, and recruiter notifications.
- **Application secrets and infrastructure handles** — session secret, database connection string, SendGrid credentials, OpenAI key, Microsoft OAuth secrets, and Bullhorn credentials.
- **AI and workflow budget** — public endpoints that can trigger OpenAI, parsing, or background processing can create direct operational cost and queue pressure.

## Trust Boundaries

- **Public browser / server** — job application forms, inbound email webhook, and any public support or health endpoints accept untrusted input from the internet.
- **Authenticated user / admin server features** — recruiter/admin dashboards, support dashboards, settings, and Bullhorn-triggering actions must enforce server-side authz.
- **Server / PostgreSQL** — application code has broad read/write access to candidate, support, audit, and configuration tables.
- **Server / third-party APIs** — Bullhorn, SendGrid, OpenAI, Microsoft Graph/OAuth, and OneDrive are all high-trust outbound integrations.
- **External email / workflow automation** — inbound email parsing crosses from untrusted SMTP-originated content into candidate ingestion, vetting conversations, and support workflows.
- **Tenant / tenant boundary** — brand/environment scoping must prevent one brand’s traffic, records, or writes from affecting another.

## Scan Anchors

- **Production entry points** — `app.py`, `routes/job_application.py`, `routes/email.py`, `routes/support_request.py`, `routes/auth.py`.
- **Highest-risk code areas** — inbound email ingestion (`routes/email.py`, `email_inbound_service/`), support workflow (`routes/support_request.py`, `scout_support_service.py`, `routes/scout_support.py`), authentication/session flows (`routes/auth.py`, `extensions.py`), Bullhorn-connected execution paths.
- **Public surfaces** — apply pages and resume parsing, job submission, inbound email webhook, support-host/public support routes, selected health/diagnostic endpoints.
- **Authenticated/admin surfaces** — recruiter dashboards, settings, Scout Support dashboard, scheduler/admin APIs, Bullhorn mutation flows.
- **Usually dev-only / lower-priority areas** — dev preview routes explicitly blocked in production, tests, migrations, and sandbox-only flows unless production reachability is proven.

## Threat Categories

### Spoofing

This app trusts data from login forms, inbound email messages, support replies, and third-party callbacks. The system must strongly authenticate recruiter/admin users and must cryptographically verify any public webhook or email-ingest source before treating sender addresses, subjects, or message bodies as trustworthy. Email-originated workflow steps MUST NOT rely on attacker-controlled `From` data alone.

### Tampering

Public forms, resumes, email bodies, and support attachments all cross into state-changing workflows. The server must validate these inputs before they create tickets, candidate records, Bullhorn writes, or AI-driven actions. Client-side expectations or domain-based UI assumptions are not sufficient protection for internal-only flows.

### Information Disclosure

The application stores candidate PII, internal support contacts, support conversations, Bullhorn-linked metadata, and secrets-derived configuration. Public endpoints and error paths must not expose internal directories, database details, workflow state, or candidate/support records. Logs and JSON debug endpoints must avoid leaking sensitive data or infrastructure hints.

### Denial of Service

Several production features accept unauthenticated input and may trigger resume parsing, AI analysis, email handling, database writes, and background processing. Public endpoints must enforce practical abuse controls such as request authentication where appropriate, bounded upload sizes, and rate limiting on login, reset, and expensive workflows. The app must prevent attackers from turning public forms or webhooks into cost-amplification or queue-flooding channels.

### Elevation of Privilege

Recruiter/admin features can create Bullhorn changes, manage support tickets, and access sensitive dashboards. These actions must remain behind server-side authentication and authorization on every reachable host. Internal-only support and approval workflows must not become reachable through alternate production domains or unauthenticated callback routes.
