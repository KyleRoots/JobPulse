# Step-by-Step: Creating Your Job Application Replit Project

## Step 1: Create New Replit Project

1. **Go to replit.com** and log in to your account
2. **Click "Create App"** on your Home screen
3. **Choose "Flask" template** (this gives you the right Python environment)
4. **Set the title**: "Myticas Job Applications" or similar
5. **Click "Create App"**

## Step 2: Upload Your Project Files

You have two options:

### Option A: File Upload (Recommended)
1. **Open the Files panel** (folder icon on left sidebar)
2. **Delete the default files** (main.py, any existing files)
3. **Drag and drop these files** from your computer to the Files panel:
   - All files from the `job_application_app/` folder
   - Or select all files and upload them

### Option B: Copy-Paste Method
1. Create each file manually in the new project
2. Copy content from each file in `job_application_app/`
3. Paste into corresponding new files

## Step 3: Set Environment Variables (Secrets)

1. **Open Secrets tool** (key icon on left sidebar)
2. **Click "New Secret"**
3. **Add these secrets:**

   **Secret 1:**
   - Key: `SENDGRID_API_KEY`
   - Value: [Copy from your current project's secrets]

   **Secret 2:**
   - Key: `SESSION_SECRET` 
   - Value: [Copy from your current project's secrets]

4. **Click "Add Secret"** for each one

## Step 4: Test Your Application

1. **Click the "Run" button** (play icon)
2. **Wait for startup** (should see "Starting gunicorn..." in console)
3. **Open the preview** (should show Myticas landing page)
4. **Test a job application URL**:
   ```
   https://your-replit-domain.replit.app/apply/34096/Test%20Job/?source=LinkedIn
   ```

## Step 5: Deploy to Custom Domain

1. **Click "Deploy"** button (rocket icon on left sidebar)
2. **Choose deployment type**: "Autoscale" (recommended) or "Reserved VM"
3. **Deploy your app** first (this creates the deployment)
4. **Go to Deployments tab** after successful deployment
5. **Click "Custom Domains"**
6. **Add custom domain**: `apply.myticas.com`
7. **Follow DNS setup instructions**:
   - Copy the A record and TXT record provided
   - Add these to your domain's DNS settings
   - Wait for DNS propagation (can take up to 48 hours)

## Step 6: Update Main Application

Once your job application service is live at `apply.myticas.com`, the main application will automatically route job applications to your new service!

## Troubleshooting

**If deployment fails:**
- Check console logs for errors
- Ensure all files are uploaded correctly
- Verify environment variables are set

**If custom domain doesn't work:**
- Double-check DNS records
- Wait longer for DNS propagation
- Contact Replit support if issues persist

## File List to Upload

Make sure you upload all these files from `job_application_app/`:

✅ `app.py`
✅ `main.py` 
✅ `job_application_service.py`
✅ `resume_parser.py`
✅ `pyproject.toml`
✅ `.replit`
✅ `templates/` folder (with all HTML files)
✅ `static/` folder (with JS and images)

## Support

If you run into any issues:
1. Check the console logs in your new Replit
2. Test locally first before deploying
3. Verify environment variables are correctly set
4. Contact Replit support for deployment-specific issues