# AI Job Classification System

## Overview
The system uses OpenAI's GPT-4o to automatically classify jobs into three categories:
- **Job Function** (e.g., Information Technology, Sales, Marketing)
- **Job Industry** (e.g., Computer Software, Healthcare, Finance)
- **Seniority Level** (e.g., Entry level, Mid-Senior level, Director)

## How It Works
1. When new jobs are added from Bullhorn, the AI analyzes the job title and description
2. The AI selects the most appropriate values from your predefined Excel lists
3. The classification is added to the XML file in three new nodes:
   - `<jobfunction>`
   - `<jobindustries>`
   - `<senoritylevel>`

## Current Status
✓ All 72 existing jobs have been classified
✓ New jobs will be automatically classified when added
✓ Classifications are tracked in the field monitoring system

## Updating Categories

### Method 1: Replace Excel File
1. Upload your updated Excel file to the `attached_assets` folder
2. Run the update script:
   ```bash
   python update_job_categories.py attached_assets/Your_Updated_File.xlsx
   ```

### Method 2: Manual Upload
Simply upload a new Excel file with the same structure:
- Sheet 1: "Job Functions" 
- Sheet 2: "Job Industries"
- Sheet 3: "Seniority Levels"

### Excel File Format
The Excel file should maintain the same structure:
- Each sheet can have multiple columns
- Values can be in any column
- Empty cells are ignored
- The system will automatically extract all unique values

## Important Notes
- The AI can ONLY use values from your Excel file - it cannot create new categories
- If a job doesn't clearly fit any category, the AI will leave the field empty
- You can update categories at any time without affecting existing classifications
- A backup of the old categories is created when you update

## Monitoring Changes
The system tracks AI classification changes just like any other field:
- Changes appear in activity logs
- Email notifications include classification updates
- Field changes show old vs new values