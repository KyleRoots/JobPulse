// Job Application Form JavaScript
document.addEventListener('DOMContentLoaded', function() {
    // File upload handling
    setupFileUpload('resumeFile', 'resumeUploadArea', 'resumeFileInfo', 'resumeFileName', true);
    setupFileUpload('coverLetterFile', 'coverLetterUploadArea', 'coverLetterFileInfo', 'coverLetterFileName', false);
    
    // Form submission
    document.getElementById('applicationForm').addEventListener('submit', handleFormSubmission);
});

function setupFileUpload(inputId, uploadAreaId, fileInfoId, fileNameId, isRequired) {
    const fileInput = document.getElementById(inputId);
    const uploadArea = document.getElementById(uploadAreaId);
    const fileInfo = document.getElementById(fileInfoId);
    const fileName = document.getElementById(fileNameId);
    
    // Drag and drop events
    uploadArea.addEventListener('dragover', function(e) {
        e.preventDefault();
        uploadArea.classList.add('dragover');
    });
    
    uploadArea.addEventListener('dragleave', function(e) {
        e.preventDefault();
        uploadArea.classList.remove('dragover');
    });
    
    uploadArea.addEventListener('drop', function(e) {
        e.preventDefault();
        uploadArea.classList.remove('dragover');
        
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleFileSelection(files[0], fileInput, uploadArea, fileInfo, fileName, isRequired);
        }
    });
    
    // Click to upload
    uploadArea.addEventListener('click', function() {
        fileInput.click();
    });
    
    // File input change
    fileInput.addEventListener('change', function(e) {
        if (e.target.files.length > 0) {
            handleFileSelection(e.target.files[0], fileInput, uploadArea, fileInfo, fileName, isRequired);
        }
    });
}

function handleFileSelection(file, fileInput, uploadArea, fileInfo, fileName, isRequired) {
    // Validate file type
    const allowedTypes = ['application/pdf', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'];
    if (!allowedTypes.includes(file.type)) {
        alert('Please select a PDF, DOC, or DOCX file.');
        return;
    }
    
    // Validate file size (10MB limit)
    const maxSize = 10 * 1024 * 1024; // 10MB in bytes
    if (file.size > maxSize) {
        alert('File size must be less than 10MB.');
        return;
    }
    
    // Update UI
    uploadArea.style.display = 'none';
    fileInfo.style.display = 'block';
    fileName.textContent = file.name;
    
    // If this is a resume upload, trigger parsing
    if (isRequired && fileInput.id === 'resumeFile') {
        parseResumeFile(file);
    }
}

function removeResumeFile() {
    const fileInput = document.getElementById('resumeFile');
    const uploadArea = document.getElementById('resumeUploadArea');
    const fileInfo = document.getElementById('resumeFileInfo');
    
    fileInput.value = '';
    uploadArea.style.display = 'block';
    fileInfo.style.display = 'none';
    
    // Clear parsed information
    clearParsedInformation();
}

function removeCoverLetterFile() {
    const fileInput = document.getElementById('coverLetterFile');
    const uploadArea = document.getElementById('coverLetterUploadArea');
    const fileInfo = document.getElementById('coverLetterFileInfo');
    
    fileInput.value = '';
    uploadArea.style.display = 'block';
    fileInfo.style.display = 'none';
}

function parseResumeFile(file) {
    // Show loading state
    const parsedInfo = document.getElementById('parsedInfo');
    parsedInfo.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i>Parsing resume...';
    parsedInfo.style.display = 'block';
    
    // Create FormData for file upload
    const formData = new FormData();
    formData.append('resume', file);
    
    // Send file to backend for parsing
    fetch('/parse-resume', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            populateFormFromParsedData(data.parsed_data);
            showValidationWarning();
        } else {
            showParsingError(data.error || 'Failed to parse resume');
        }
    })
    .catch(error => {
        console.error('Error parsing resume:', error);
        showParsingError('Error parsing resume. Please fill in the form manually.');
    });
}

function populateFormFromParsedData(parsedData) {
    const parsedInfo = document.getElementById('parsedInfo');
    let infoHtml = '<h5><i class="fas fa-magic me-2"></i>Information Extracted from Resume</h5>';
    infoHtml += '<p>We\'ve automatically filled in some fields from your resume. Please verify the information below is correct.</p>';
    infoHtml += '<ul class="mb-0">';
    
    // Populate form fields and show what was extracted
    if (parsedData.first_name) {
        document.getElementById('firstName').value = parsedData.first_name;
        infoHtml += `<li><strong>First Name:</strong> ${parsedData.first_name}</li>`;
    }
    
    if (parsedData.last_name) {
        document.getElementById('lastName').value = parsedData.last_name;
        infoHtml += `<li><strong>Last Name:</strong> ${parsedData.last_name}</li>`;
    }
    
    if (parsedData.email) {
        document.getElementById('email').value = parsedData.email;
        infoHtml += `<li><strong>Email:</strong> ${parsedData.email}</li>`;
    }
    
    if (parsedData.phone) {
        document.getElementById('phone').value = parsedData.phone;
        infoHtml += `<li><strong>Phone:</strong> ${parsedData.phone}</li>`;
    }
    
    infoHtml += '</ul>';
    parsedInfo.innerHTML = infoHtml;
}

function showParsingError(errorMessage) {
    const parsedInfo = document.getElementById('parsedInfo');
    parsedInfo.innerHTML = `
        <h5><i class="fas fa-exclamation-triangle me-2"></i>Resume Parsing Notice</h5>
        <p>${errorMessage}</p>
        <p class="mb-0">Please fill in the form fields manually.</p>
    `;
    parsedInfo.className = 'alert alert-warning';
}

function clearParsedInformation() {
    const parsedInfo = document.getElementById('parsedInfo');
    parsedInfo.style.display = 'none';
    
    // Clear form fields
    document.getElementById('firstName').value = '';
    document.getElementById('lastName').value = '';
    document.getElementById('email').value = '';
    document.getElementById('phone').value = '';
    
    // Hide validation warning
    document.getElementById('validationWarning').style.display = 'none';
}

function showValidationWarning() {
    document.getElementById('validationWarning').style.display = 'block';
}

function handleFormSubmission(e) {
    e.preventDefault();
    
    // Show loading state
    const submitBtn = document.getElementById('submitBtn');
    const submitText = document.getElementById('submitText');
    const loadingSpinner = document.getElementById('loadingSpinner');
    
    submitBtn.disabled = true;
    submitText.style.display = 'none';
    loadingSpinner.style.display = 'inline';
    
    // Prepare form data
    const formData = new FormData();
    
    // Add form fields
    formData.append('firstName', document.getElementById('firstName').value);
    formData.append('lastName', document.getElementById('lastName').value);
    formData.append('email', document.getElementById('email').value);
    formData.append('phone', document.getElementById('phone').value);
    
    // Add files
    const resumeFile = document.getElementById('resumeFile').files[0];
    const coverLetterFile = document.getElementById('coverLetterFile').files[0];
    
    if (resumeFile) {
        formData.append('resume', resumeFile);
    }
    
    if (coverLetterFile) {
        formData.append('coverLetter', coverLetterFile);
    }
    
    // Add job information from URL
    const urlParts = window.location.pathname.split('/');
    const jobId = urlParts[2]; // /apply/[jobId]/[title]/
    const jobTitle = urlParts[3];
    const urlParams = new URLSearchParams(window.location.search);
    const source = urlParams.get('source') || '';
    
    formData.append('jobId', jobId);
    formData.append('jobTitle', decodeURIComponent(jobTitle));
    formData.append('source', source);
    
    // Submit form
    fetch('/submit-application', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Show success modal
            const successModal = new bootstrap.Modal(document.getElementById('successModal'));
            successModal.show();
        } else {
            alert('Error submitting application: ' + (data.error || 'Unknown error'));
            resetSubmitButton();
        }
    })
    .catch(error => {
        console.error('Error submitting application:', error);
        alert('Error submitting application. Please try again.');
        resetSubmitButton();
    });
}

function resetSubmitButton() {
    const submitBtn = document.getElementById('submitBtn');
    const submitText = document.getElementById('submitText');
    const loadingSpinner = document.getElementById('loadingSpinner');
    
    submitBtn.disabled = false;
    submitText.style.display = 'inline';
    loadingSpinner.style.display = 'none';
}