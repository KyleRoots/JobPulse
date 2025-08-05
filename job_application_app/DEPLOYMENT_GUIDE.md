# Deployment Guide: Job Application Service

## Quick Deployment Steps

### Option 1: New Replit Project (Recommended)

1. **Create New Replit**
   - Go to replit.com
   - Create new project: "Myticas Job Applications"
   - Choose "Flask" template

2. **Upload Project Files**
   ```bash
   # Copy all files from job_application_app/ to new replit
   # Or zip and upload the entire job_application_app folder
   ```

3. **Set Environment Variables**
   - Go to Secrets tab in new Replit
   - Add: `SENDGRID_API_KEY` (copy from main project)
   - Add: `SESSION_SECRET` (copy from main project)

4. **Deploy to Custom Domain**
   - Click Deploy button
   - Configure custom domain: `apply.myticas.com`
   - Replit handles SSL automatically

### Option 2: Same Replit with Subdirectory

1. **Keep in current project**
   - Files already in `job_application_app/` folder
   - Create separate workflow for this subdirectory
   - Configure subdomain routing

### Option 3: External Hosting

**For services like Heroku, DigitalOcean, AWS:**

1. **Upload project**
2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
3. **Set environment variables**
4. **Run with:**
   ```bash
   gunicorn --bind 0.0.0.0:5000 main:app
   ```

## Benefits of Separation

✅ **Security**: Public forms isolated from admin functions  
✅ **Performance**: Lightweight service for high traffic  
✅ **Scaling**: Independent resource allocation  
✅ **Maintenance**: Focused codebase for easier updates  
✅ **Domain Strategy**: Clean separation (apply.myticas.com vs jobpulse.lyntrix.ai)  

## URL Integration

The main app automatically generates URLs pointing to the job application service:

```python
# Main app generates:
https://apply.myticas.com/apply/34096/AI%20Scrum%20Master/?source=LinkedIn

# Job app handles:
- Resume parsing
- Form submission  
- Email notifications
- Mobile-optimized UI
```

## Testing

After deployment, test these endpoints:

- `https://apply.myticas.com/` - Landing page
- `https://apply.myticas.com/apply/34096/Test%20Job/` - Application form
- `https://apply.myticas.com/health` - Health check

## Monitoring

The job application service logs all activities:
- Form submissions
- Resume parsing results
- Email delivery status
- Error tracking