---
description: How to run JobPulse locally with ngrok for testing
---

# Local Development Testing Workflow

## Prerequisites
- PostgreSQL running (`brew services list | grep postgresql`)
- ngrok installed (`ngrok --version`)
- Virtual environment ready (Python 3.11)

## Quick Start

### 1. Start the Flask App
// turbo
```bash
cd /Users/rooster/.gemini/antigravity/playground/JobPulse
source .venv/bin/activate
python main.py
```
App runs at: http://127.0.0.1:5001

### 2. Start ngrok with Static Domain (separate terminal)
Use the whitelisted static domain for Bullhorn OAuth:
```bash
ngrok http 5001 --domain=unenergetically-nonfeudal-winter.ngrok-free.dev
```
Public URL: `https://unenergetically-nonfeudal-winter.ngrok-free.dev`

> **Note**: This domain is whitelisted with Bullhorn (Ticket #05991506, confirmed Feb 2, 2026)

### 3. Environment Configuration
Ensure `.env` has these settings:
```
OAUTH_REDIRECT_BASE_URL=https://unenergetically-nonfeudal-winter.ngrok-free.dev
BULLHORN_USE_NEW_API=true
ADMIN_PASSWORD=MyticasXML2025!
```

### 4. Test Login & Bullhorn Connection
1. Open http://127.0.0.1:5001/login (or the ngrok URL)
2. Login with: `admin` / `MyticasXML2025!`
3. Go to ATS Settings → Test Connection (initiates OAuth flow)
4. Login to Bullhorn: `Kyle.Roots.Myticas` / `Myticas01!`
5. Should see green success messages confirming connection

### 5. Test SendGrid Email Parsing (optional)
Update SendGrid Inbound Parse settings to:
```
https://unenergetically-nonfeudal-winter.ngrok-free.dev/api/parse-email
```

## Health Check Commands
// turbo
```bash
curl http://127.0.0.1:5001/health
curl http://127.0.0.1:5001/api/system/health
```

## Whitelisted Redirect URIs (Bullhorn)
Client ID: `676f3cfc-c611-4d23-a8bc-9b595a01d4ab`
- `https://jobpulse.lyntrix.ai/bullhorn/oauth/callback` (production)
- `https://unenergetically-nonfeudal-winter.ngrok-free.dev/bullhorn/oauth/callback` (local dev)

## Stop Services
- Flask: Ctrl+C
- ngrok: Ctrl+C
