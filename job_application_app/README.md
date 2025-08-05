# Myticas Job Application Service

A standalone Flask application for handling job applications at apply.myticas.com.

## Overview

This is a lightweight, focused service that handles:
- Job application forms with responsive design
- Resume parsing and auto-population
- Email submission to apply@myticas.com
- File uploads (resume and cover letter)

## Architecture

**Separate from Main Application:**
- Main app (jobpulse.lyntrix.ai): Admin, monitoring, XML processing
- This app (apply.myticas.com): Public job application forms only

## Features

- **Responsive Design**: Perfect mobile UI with ultra-tight professional spacing
- **Resume Parsing**: Automatic extraction of name, email, phone from uploads
- **File Handling**: Secure upload processing for PDF and Word documents
- **Email Integration**: SendGrid-powered notifications with attachments
- **Error Handling**: Comprehensive validation and user-friendly messages

## Project Structure

```
job_application_app/
├── app.py                    # Main Flask application
├── main.py                   # Entry point (Replit convention)
├── job_application_service.py # Business logic
├── resume_parser.py          # Resume processing
├── pyproject.toml           # Dependencies
├── .replit                  # Deployment config
├── templates/               # HTML templates
│   ├── index.html          # Landing page
│   ├── apply.html          # Application form
│   ├── 404.html            # Error pages
│   └── 500.html
└── static/                 # Assets
    ├── js/
    │   └── job-application.js
    ├── css/
    └── images/
        └── myticas-logo-bw-revised.png
```

## Environment Variables

Required environment variables:
- `SENDGRID_API_KEY`: For email notifications
- `SESSION_SECRET`: For Flask sessions

## Deployment

### Option 1: Replit Deployment
1. Create new Replit project
2. Upload this folder contents
3. Set environment variables
4. Deploy to custom domain: apply.myticas.com

### Option 2: Manual Deployment
```bash
cd job_application_app
pip install -r requirements.txt  # or use uv
gunicorn --bind 0.0.0.0:5000 main:app
```

## API Endpoints

- `GET /` - Landing page
- `GET /apply/<job_id>/<job_title>/` - Application form
- `POST /parse-resume` - Resume parsing API
- `POST /submit-application` - Form submission
- `GET /health` - Health check

## Integration with Main App

The main application generates URLs pointing to this service:
```python
# In main app's XML generation:
url = f"https://apply.myticas.com/apply/{job_id}/{encoded_title}/?source=LinkedIn"
```

## Benefits of Separation

1. **Security**: Public forms isolated from admin functions
2. **Performance**: Lightweight service for public traffic
3. **Scaling**: Independent deployment and scaling
4. **Maintenance**: Focused codebase for easier updates
5. **Domain Strategy**: Clean subdomain separation

## Development

```bash
# Install dependencies
uv sync

# Run development server
python main.py

# Access at http://localhost:5001
```