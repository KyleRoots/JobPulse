# JobPulse Dual-Domain Deployment Guide

## Architecture Overview

**Main Application (jobpulse.lyntrix.ai):**
- Admin dashboard and monitoring
- XML processing and Bullhorn integration
- Email delivery logs and file management
- User authentication and settings

**Job Applications (apply.myticas.com):**
- Public job application forms
- Resume parsing and candidate submission
- Clean, branded experience for job seekers

## Deployment Steps

### Step 1: Deploy Main App to jobpulse.lyntrix.ai

1. **Create Deployment in Replit:**
   - Click "Deploy" button in your workspace
   - Choose "Autoscale Deployment" or "Reserved VM Deployment"
   - Set run command: `gunicorn --bind 0.0.0.0:5000 --reuse-port --reload main:app`

2. **Configure Custom Domain:**
   - Go to Deployments → Settings → "Link a domain"
   - Enter: `jobpulse.lyntrix.ai`
   - Add the provided DNS records to your domain registrar:
     - A record pointing to Replit's IP
     - TXT record for verification

3. **Set Environment Variables:**
   ```
   REPLIT_ENVIRONMENT=production
   JOB_APPLICATION_BASE_URL=https://apply.myticas.com
   ```

### Step 2: Deploy Job Applications to apply.myticas.com

**Option A: Separate Replit Project (Recommended)**
1. Create new Replit project for job applications only
2. Copy these files:
   - `app.py` (job application routes only)
   - `templates/apply.html`
   - `job_application_service.py`
   - `resume_parser.py`
   - `models.py` (User, BullhornJob models only)
3. Deploy to `apply.myticas.com`

**Option B: Same Project, Different Deployment**
1. Create second deployment from same project
2. Configure to serve only job application routes
3. Deploy to `apply.myticas.com`

### Step 3: Update XML Generation

The system will automatically generate URLs pointing to `apply.myticas.com` when deployed in production mode.

## Environment Configuration

### Development (Current Setup):
```bash
# Uses current Replit domain for immediate testing
JOB_APPLICATION_BASE_URL=https://your-replit-domain.replit.dev
```

### Production:
```bash
# Main app
REPLIT_ENVIRONMENT=production
JOB_APPLICATION_BASE_URL=https://apply.myticas.com

# Job application app
REPLIT_ENVIRONMENT=production
DATABASE_URL=your-shared-database-url
```

## Testing the Setup

1. **Main App**: Access admin features at `https://jobpulse.lyntrix.ai`
2. **Job Applications**: Test form at `https://apply.myticas.com/apply/34083/Internal%20Audit%20Consultant/`
3. **XML Feed**: Verify URLs in `https://myticas.com/myticas-job-feed.xml`

## Benefits of This Architecture

- **Separation of Concerns**: Admin vs public functionality
- **Clean Branding**: Professional URLs for job seekers
- **Scalability**: Each domain can be optimized independently
- **Security**: Admin functions isolated from public forms