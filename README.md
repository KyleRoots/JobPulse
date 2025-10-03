# XML Job Feed Reference Number Updater

## 📋 Overview

A comprehensive Flask-based web application that automates XML job feed processing, reference number management, and synchronization with Bullhorn ATS/CRM. The system ensures accurate job listings, maintains real-time synchronization, and streamlines application workflows to enhance job visibility and operational efficiency.

### Primary Capabilities
- **Automated XML Job Feed Updates**: 30-minute cycle with SFTP uploads
- **Database-Backed Reference Number Preservation**: Persistent reference numbers across all cycles
- **Bullhorn ATS Integration**: Real-time job data synchronization from multiple tearsheets
- **Production Monitoring**: Health checks with email alerts to kroots@myticas.com
- **Job Application Forms**: Public-facing responsive forms with resume parsing
- **Intelligent Job Classification**: Keyword-based categorization system

---

## 🏗️ System Architecture

### Backend Stack
- **Web Framework**: Flask (Python 3.x)
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Authentication**: Flask-Login for secure user sessions
- **Background Processing**: APScheduler for automated tasks
- **XML Processing**: lxml library with CDATA formatting
- **Email Service**: SendGrid for notifications
- **SFTP Service**: Built-in secure file transfer
- **ATS Integration**: Bullhorn API with tearsheet monitoring

### Frontend Stack
- **Template Engine**: Jinja2
- **UI Framework**: Bootstrap 5 (dark theme)
- **Icons**: Font Awesome 6.0
- **Client-side**: Vanilla JavaScript for interactive features

### Infrastructure
- **Session Management**: Flask sessions with secure keys
- **Proxy Support**: ProxyFix middleware for HTTPS
- **File Handling**: Secure temporary storage with auto-cleanup
- **Dual-Domain Setup**: jobpulse.lyntrix.ai (main) + apply.myticas.com (applications)

---

## 🚀 Core Features

### 1. Automated Upload System (30-Minute Cycle) - Toggle-Based
- **Scheduler-Backed Automation**: APScheduler runs upload cycle every 30 minutes when enabled
- **Settings Control**: Requires BOTH `automated_uploads_enabled=true` AND `sftp_enabled=true`
- **Fresh XML Generation**: Pulls from all Bullhorn tearsheets (1256, 1264, 1499, 1556)
- **Reference Number Preservation**: Database-backed persistence ensures no reversion
- **SFTP Upload**: Secure automated uploads to production server (when automation is enabled)
- **Manual Workflow Alternative**: Can be disabled for manual-only downloads via settings toggle

### 2. Database-Backed Reference Number System ✨ NEW
**Problem Solved (October 2025)**: Live XML URL returns 403 Forbidden, causing reference numbers to revert to old values.

**Solution Architecture**:
- `JobReferenceNumber` database table stores all reference numbers persistently
- Manual refresh saves reference numbers to database after generation
- 30-minute automated upload reads from database instead of protected URL
- Result: Reference numbers remain consistent across all cycles

**How It Works**:
1. **Manual Refresh**: Generates fresh XML → Applies reference numbers → Saves to database → Uploads to production
2. **Automated Upload (when enabled)**: Generates fresh XML → Loads references from database → Applies to XML → Saves to database → Uploads to production
3. **Fallback**: If database read fails, attempts to read from published XML file
4. **Result**: Reference numbers persist across all cycles with no reversion

**Database Schema**:
- Table: `JobReferenceNumber`
- Fields: job_id (unique), reference_number, last_updated
- Purpose: Persistent storage of all job reference numbers

### 3. Bullhorn ATS Integration
- **Fresh Data Generation**: Pulls from tearsheets 1256, 1264, 1499, 1556 on-demand
- **Multi-Tearsheet Support**: Comprehensive job data from all configured sources
- **HTML Parsing**: Proper tag closure with lxml for description fields
- **CDATA Wrapping**: All XML fields properly wrapped for data integrity
- **Company Name Formatting**: "STSI (Staffing Technical Services Inc.)" for tearsheet 1556
- **Real-time Processing**: Generates XML when manual refresh or automated cycle executes

### 4. Production Environment Monitoring
- **Health Check Endpoints**: `/health`, `/ready`, `/alive`, `/ping`
- **Email Notifications**: Automated alerts to kroots@myticas.com for:
  - Production environment downtime
  - Recovery notifications
  - Upload status updates
- **Optimized Monitoring**: Health checks every 2 hours (optimized for manual workflow)
- **Scheduler Auto-Restart**: Automatic recovery with timeout protection

### 5. Job Application System
- **Resume Parsing**: Extracts contact info from Word/PDF formats
- **Auto-Population**: Candidate fields automatically filled from resume
- **Bullhorn Integration**: Direct job ID integration
- **Responsive Design**: Mobile-optimized application forms
- **Unique Branding**: Customizable for client-specific needs

### 6. Internal Job Classification
- **Keyword-Based System**: Instant categorization without external APIs
- **Classification Fields**: jobfunction, jobindustries, senioritylevel
- **Reliable & Fast**: No API dependencies or rate limits

### 7. Intelligent File Management
- **Automated Consolidation**: Merges and optimizes XML files
- **Duplicate Detection**: Prevents job pollution with orphan prevention
- **Temporary File Cleanup**: Automatic storage optimization
- **Secure File Handling**: Validated uploads with size constraints (max 50MB)

---

## 📊 Database Schema

### Core Models

#### JobReferenceNumber (NEW - October 2025)
```python
class JobReferenceNumber(db.Model):
    id = Integer (Primary Key)
    job_id = String(255) (Unique, Indexed)
    reference_number = String(50)
    last_updated = DateTime
```
Stores reference numbers persistently to prevent reversion issues.

#### GlobalSettings
Stores system configuration:
- SFTP credentials (hostname, username, password, directory, port)
- Email settings (notifications enabled, default email)
- Automation settings (uploads enabled/disabled)

#### User
Flask-Login authentication model:
- User credentials and session management

#### ActivityLog
Tracks all system activities:
- Upload events, refresh operations, errors
- Timestamp tracking for audit trail

#### UploadSchedule
Manages scheduled upload configurations

---

## 🔧 Technical Implementation

### XML Processing Engine
- **Root Element**: Requires 'source' element
- **Required Fields**: title, company, date, referencenumber
- **CDATA Formatting**: All fields wrapped for proper data handling
- **HTML Consistency**: lxml parser ensures proper tag closure
- **Reference Preservation**: Database-backed lookup system

### Reference Number Generation
```python
# Manual Refresh Flow:
1. Generate fresh XML from Bullhorn
2. Apply reference number refresh
3. Save to JobReferenceNumber table
4. Upload to production

# Automated Upload Flow (30 min):
1. Generate fresh XML from Bullhorn
2. Load reference numbers from database
3. Apply to XML content
4. Upload to production
```

### Upload Workflow
- **File Upload Constraints**: XML only, max 50MB
- **Temporary Storage**: Secure filename handling with auto-cleanup
- **SFTP Protocol**: Thread-safe uploads to production server
- **Environment Detection**: Auto-detects dev/production for correct filename

### Error Handling
- **XML Syntax Validation**: Comprehensive error catching
- **User-Friendly Messages**: Non-technical error reporting
- **Server-Side Logging**: Detailed debug information
- **Client-Side Validation**: Real-time form validation

---

## 📦 Installation & Setup

### Prerequisites
- Python 3.x
- PostgreSQL database
- SFTP server credentials
- SendGrid API key (for email notifications)
- Bullhorn credentials

### Environment Variables
```bash
DATABASE_URL=postgresql://user:pass@host:port/dbname
SESSION_SECRET=your-secret-key
BULLHORN_PASSWORD=your-bullhorn-password
SENDGRID_API_KEY=your-sendgrid-key
```

### Python Dependencies
```bash
apscheduler
email-validator
flask
flask-dance
flask-login
flask-sqlalchemy
gunicorn
lxml
oauthlib
openpyxl
pandas
paramiko
psycopg2-binary
pyjwt
pypdf2
python-docx
requests
sendgrid
sqlalchemy
werkzeug
```

### Database Initialization
```bash
# Database tables are created automatically on first run
# JobReferenceNumber table added October 2025
python main.py
```

### Running the Application
```bash
# Development
gunicorn --bind 0.0.0.0:5000 --reuse-port --reload main:app

# The application listens on port 5000
# Access at: http://localhost:5000
```

---

## 🚀 Deployment

### Publishing to Production

#### Step 1: Configure Settings
Navigate to Settings page and configure:
- **SFTP Credentials**: hostname, username, password, directory, port
- **Email Notifications**: Enable notifications, set default email (kroots@myticas.com)
- **Automation Toggle**: Enable/disable automated uploads as needed

#### Step 2: Enable Automated Uploads (Optional)
**Required for 30-minute automation**:
1. ✅ Check "Enable SFTP Uploads" 
2. ✅ Check "Enable Automated Uploads"
3. ✅ Save settings

**Note**: Both toggles must be ON for automated uploads to run. If either is OFF, the system runs in manual-only mode.

#### Step 3: Populate Reference Number Database
1. Click "Refresh All Reference Numbers" button on dashboard
2. Verify success message
3. Confirms JobReferenceNumber table is populated

#### Step 4: Deploy Application
Publish/Deploy via Replit to production environment

#### Step 5: Verify Automation Status
Check dashboard for automation status:
- **"Active"** = Automation running, next upload scheduled
- **"Inactive"** = Manual-only mode (check settings toggles)

### Post-Deployment Verification
- ✅ Check automation status on dashboard (should show "Active")
- ✅ Verify SFTP credentials are working
- ✅ Confirm email notifications are being received
- ✅ Test manual refresh and verify database persistence
- ✅ Monitor next 30-minute cycle for reference preservation

---

## 🔍 Troubleshooting Guide

### Reference Numbers Keep Reverting
**Problem**: Reference numbers change back to old values like "EXZNVDOWMS"

**Solution**: ✅ SOLVED (October 2025)
- Database-backed preservation system prevents reversion
- Manual refresh populates JobReferenceNumber table
- Automated uploads read from database, not protected URL

### Automated Uploads Not Working
**Symptoms**: Dashboard shows "Inactive" status

**Root Cause**: Automation requires BOTH settings to be enabled

**Solutions**:
1. **Verify Settings Toggles**:
   - Go to Settings page
   - ✅ Check "Enable SFTP Uploads" is ON
   - ✅ Check "Enable Automated Uploads" is ON
   - Save settings
   
2. **Verify SFTP Configuration**:
   - Hostname, username, password must be filled
   - Directory path must be valid
   - Port must be correct (default: 2222)

3. **Check Scheduler Status**:
   - Logs should show: "📤 Scheduled automated uploads every 30 minutes"
   - If missing, restart application to reinitialize scheduler

4. **Manual Test**:
   - Click "Refresh All" button to test SFTP connection
   - If successful, automation should work on next cycle

**Note**: The scheduler ALWAYS runs every 30 minutes, but skips execution if settings are disabled. Check logs for: "📋 Automated uploads disabled in settings, skipping upload cycle"

### Email Notifications Not Sending
**Check**:
1. Settings → Email Notifications Enabled = ON
2. Default notification email is set to kroots@myticas.com
3. SendGrid API key is configured
4. Check email service logs for delivery status

### Production Monitoring Alerts
**Email Alerts Sent For**:
- Production environment becomes unreachable
- Production environment recovers
- Automated upload success/failure

**Health Endpoints**:
- `/health` - Overall system health
- `/ready` - Database connectivity check
- `/alive` - Basic application responsiveness
- `/ping` - Ultra-fast availability check

### Database Connection Issues
**Error**: "Database connection failed"

**Solutions**:
1. Verify DATABASE_URL environment variable
2. Check PostgreSQL is running
3. Confirm database credentials are correct
4. Review connection pool settings (300s recycle, pre-ping enabled)

### SFTP Upload Failures
**Common Issues**:
- Incorrect hostname/port (default: 2222)
- Invalid credentials
- Target directory permissions
- Network connectivity

**Debug Steps**:
1. Test SFTP connection manually
2. Verify directory path exists
3. Check application logs for detailed error
4. Confirm SFTP mode is enabled (not FTP)

### XML Generation Errors
**Validation Errors**:
- Missing required fields (title, company, date, referencenumber)
- Invalid root element (must be 'source')
- Unclosed HTML tags → Auto-fixed by lxml parser
- CDATA formatting issues → Auto-wrapped by system

---

## 📈 Recent Major Updates

### October 2025: Database-Backed Reference Number Preservation ✨
- **Problem Identified**: Live XML URL returns 403 Forbidden, causing reference number reversion
- **Solution Implemented**: JobReferenceNumber database table for persistent storage
- **Impact**: Reference numbers now preserved across all cycles (manual and automated)
- **Manual Refresh Workflow**: Generates XML → Saves reference numbers to database → Uploads
- **Automated Upload Workflow**: Generates XML → Loads from database → Applies references → Saves → Uploads
- **Fallback Logic**: Reads from published XML file if database read fails

### September 2025: Toggle-Based Automation Architecture
- **30-Minute Upload Cycle**: APScheduler-backed automation with settings control
- **Dual Toggle System**: Requires both `automated_uploads_enabled` AND `sftp_enabled`
- **Manual Workflow Support**: Can be fully disabled for manual-only operations
- **Production Monitoring**: Health checks every 2 hours with email alerts to kroots@myticas.com
- **Dashboard Enhancement**: Real-time automation status display (Active/Inactive)

### September 2025: Enhanced XML Processing
- **CDATA Wrapping**: All XML fields properly formatted
- **HTML Parsing**: lxml integration for proper tag closure
- **Multi-Tearsheet Support**: Pulls from all Bullhorn sources
- **Company Name Formatting**: Proper STSI branding

### User Experience Improvements
- **Login Redirect**: Changed from ATS Monitoring to main Dashboard
- **Manual Workflow**: 30-minute automation optimized for manual downloads
- **Change Notifications**: Email alerts only during actual downloads
- **Dashboard Status**: Accurate "Active/Inactive" automation display

---

## 🔐 Security Features

- **OAuth Authentication**: Secure user login with Flask-Login
- **Session Management**: Encrypted session keys
- **SFTP Protocol**: Secure file transfers (not FTP)
- **Password Hashing**: Werkzeug security for user passwords
- **Secret Management**: Environment-based secret storage
- **ProxyFix Middleware**: Proper HTTPS handling

---

## 📞 Support & Monitoring

### System Administrator
**Email**: kroots@myticas.com

**Receives Notifications For**:
- Production environment downtime/recovery
- Automated upload status (success/failure)
- Critical system errors

### Application URLs
- **Main Application**: jobpulse.lyntrix.ai
- **Job Application Forms**: apply.myticas.com

### Health Monitoring
Access health endpoints for status checks:
- `https://jobpulse.lyntrix.ai/health`
- `https://jobpulse.lyntrix.ai/ready`
- `https://jobpulse.lyntrix.ai/alive`
- `https://jobpulse.lyntrix.ai/ping`

---

## 📝 Development Notes

### Code Conventions
- **Framework**: Flask with Jinja2 templates
- **Database ORM**: SQLAlchemy with declarative base
- **XML Library**: lxml for robust parsing
- **Background Jobs**: APScheduler with interval triggers
- **Email Service**: SendGrid Python SDK

### File Structure
```
├── app.py                          # Main Flask application
├── main.py                         # Application entry point
├── models.py                       # Database models
├── email_service.py                # SendGrid email integration
├── ftp_service.py                  # SFTP upload service
├── bullhorn_service.py             # Bullhorn ATS integration
├── simplified_xml_generator.py     # XML generation engine
├── lightweight_reference_refresh.py # Reference number management
├── templates/                      # Jinja2 templates
│   ├── dashboard.html
│   ├── settings.html
│   └── ...
└── static/                         # CSS, JS, assets
```

### Key Design Decisions
1. **Database-Backed References**: Prevents reversion when live URL is protected
2. **30-Minute Automation**: Balances freshness with system load
3. **Manual Workflow Focus**: Optimized for user-initiated downloads
4. **SFTP Over FTP**: Security and thread-safety requirements
5. **Dual-Domain Architecture**: Separates main app from application forms

---

## 🎯 Future Enhancements

### Planned Features
- Enhanced analytics dashboard with job trend visualization
- Advanced filtering and search capabilities
- Multi-tenant support for different clients
- API endpoints for third-party integrations
- Automated reporting system

### Optimization Opportunities
- Redis caching for frequently accessed data
- Celery for distributed task processing
- GraphQL API for flexible data queries
- Real-time WebSocket updates for dashboard

---

## 📄 License & Credits

**Created**: 2025  
**Maintained By**: Development Team  
**Contact**: kroots@myticas.com

---

**Last Updated**: October 2025  
**Version**: 2.0 (Database-Backed Reference Preservation)
