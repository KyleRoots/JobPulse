# XML Job Feed Reference Number Updater

## Overview

This is a Flask-based web application designed to process XML job feed files and update reference numbers. The application provides a user-friendly interface for uploading XML files, validating their structure, and processing them to ensure proper reference number formatting.

## System Architecture

### Frontend Architecture
- **Template Engine**: Jinja2 templates with Bootstrap 5 dark theme
- **Client-side**: Vanilla JavaScript for drag-and-drop file upload functionality
- **UI Framework**: Bootstrap 5 with custom CSS styling and responsive design
- **Icons**: Font Awesome 6.0 for visual elements
- **Mobile Support**: Responsive design with tablet/mobile breakpoints and touch-friendly interfaces

### Backend Architecture
- **Web Framework**: Flask (Python)
- **Database**: PostgreSQL with SQLAlchemy ORM for schedule and log management
- **Background Processing**: APScheduler for automated XML processing
- **File Processing**: Custom XML processor using lxml library
- **Email Service**: SendGrid integration for processing notifications
- **FTP Service**: Built-in FTP client for automatic file uploads to hosting providers
- **Session Management**: Flask sessions with configurable secret key
- **File Handling**: Temporary file storage with secure filename handling
- **Proxy Support**: ProxyFix middleware for deployment behind reverse proxies

## Key Components

### 1. Flask Application (`app.py`)
- Main application entry point
- Route handlers for file upload and processing
- Security configurations (file size limits, allowed extensions)
- Error handling and user feedback via flash messages

### 2. XML Processor (`xml_processor.py`)
- Validates XML structure and required elements
- Processes job feed files with reference number generation
- Handles XML parsing errors and validation logic
- Maintains tracking of generated reference numbers

### 3. Frontend Interface (`templates/index.html`, `static/script.js`)
- Drag-and-drop file upload interface
- Real-time file validation feedback
- Progress indicators for processing
- Responsive design with dark theme

### 4. Application Entry Point (`main.py`)
- Simple import module for deployment compatibility

## Data Flow

1. **File Upload**: User selects XML file via drag-and-drop or file picker
2. **Client Validation**: JavaScript validates file type and size
3. **Server Processing**: Flask receives file and stores in temporary directory
4. **XML Validation**: XMLProcessor validates structure and required elements
5. **Reference Number Processing**: System generates/updates reference numbers
6. **Response**: User receives feedback on processing status

## External Dependencies

### Python Libraries
- **Flask**: Web framework for HTTP handling
- **lxml**: XML parsing and manipulation
- **Werkzeug**: WSGI utilities and secure file handling

### Frontend Libraries
- **Bootstrap 5**: UI framework with dark theme
- **Font Awesome 6**: Icon library

## Deployment Strategy

### Configuration
- Environment-based secret key configuration
- Proxy-aware setup for reverse proxy deployments
- Configurable upload limits and file restrictions

### File Storage
- Temporary file storage using system temp directory
- Secure filename handling to prevent directory traversal
- Automatic cleanup of temporary files

### Security Features
- File type validation (XML only)
- File size limits (50MB maximum)
- Secure filename sanitization
- Session-based flash messaging

## Changelog

```
Changelog:
- July 04, 2025. Initial setup and development
- July 04, 2025. Completed XML reference number updater with CDATA preservation and proper formatting
- July 04, 2025. Implemented comprehensive scheduling system with database integration and automated processing
- July 04, 2025. Added responsive design for mobile-friendly file management across all interfaces
- July 04, 2025. Enhanced SFTP/FTP system with dual protocol support and WP Engine optimization
- July 04, 2025. Fixed email notifications (verified sender domain) and SFTP connection (port 2222)
- July 04, 2025. Completed full automation: XML processing → Email notifications → SFTP upload to WP Engine
- July 04, 2025. Fixed filename preservation issue - now maintains original filename (myticas-job-feed-dice.xml)
- July 04, 2025. Added progress indicator modal for manual processing operations
- July 04, 2025. Implemented automated workflow integration for both manual and scheduled processing
- July 05, 2025. Added file replacement functionality - users can now update XML files without affecting schedule settings
- July 05, 2025. Implemented real-time progress tracking for manual operations with live status updates
- July 05, 2025. Fixed contrast issues for better text readability on dark backgrounds
- July 05, 2025. Added global settings page for centralized SFTP and email configuration management
- July 05, 2025. Enhanced manual uploads with SFTP auto-upload and preserved original filenames (no "updated_" prefix)
- July 05, 2025. Fixed SFTP connection testing to use proper SFTP protocol with real-time form validation
- July 05, 2025. Reorganized settings page layout with test connection section positioned after SFTP settings
- July 05, 2025. Updated email settings header to clarify automation-only usage (scheduled vs manual processing distinction)
```

## User Preferences

```
Preferred communication style: Simple, everyday language.
```

## Technical Notes

### XML Processing Requirements
- Root element must be 'source'
- Required job elements: title, company, date, referencenumber
- Validation checks first 10 jobs for performance
- Error logging for debugging and monitoring

### File Upload Constraints
- Only XML files accepted
- Maximum file size: 50MB
- Files stored temporarily during processing
- Drag-and-drop and traditional file picker support

### Error Handling
- Comprehensive XML syntax error catching
- User-friendly error messages via flash system
- Server-side logging for debugging
- Client-side validation feedback