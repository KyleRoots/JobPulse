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
    
    if (!fileInput || !uploadArea || !fileInfo || !fileName) {
        console.error('File upload elements not found:', inputId);
        return;
    }
    
    // Drag and drop events
    uploadArea.addEventListener('dragover', function(e) {
        e.preventDefault();
        e.stopPropagation();
        uploadArea.classList.add('dragover');
    });
    
    uploadArea.addEventListener('dragleave', function(e) {
        e.preventDefault();
        e.stopPropagation();
        uploadArea.classList.remove('dragover');
    });
    
    uploadArea.addEventListener('drop', function(e) {
        e.preventDefault();
        e.stopPropagation();
        uploadArea.classList.remove('dragover');
        
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            handleFileSelection(files[0], fileInput, uploadArea, fileInfo, fileName, isRequired);
        }
    });
    
    // Click to upload - only on the upload area, not the button
    uploadArea.addEventListener('click', function(e) {
        // Don't trigger if clicking on the browse button
        if (!e.target.closest('button')) {
            fileInput.click();
        }
    });
    
    // File input change
    fileInput.addEventListener('change', function(e) {
        console.log('File input changed:', e.target.files.length);
        if (e.target.files && e.target.files.length > 0) {
            handleFileSelection(e.target.files[0], fileInput, uploadArea, fileInfo, fileName, isRequired);
        }
    });
}

function handleFileSelection(file, fileInput, uploadArea, fileInfo, fileName, isRequired) {
    console.log('Handling file selection:', file.name, file.type, file.size);
    
    // Validate file type
    const allowedTypes = ['application/pdf', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'];
    
    // Also allow common alternate MIME types
    const additionalTypes = ['application/pdf', 'text/plain', 'application/octet-stream'];
    const fileExtension = file.name.toLowerCase().split('.').pop();
    const allowedExtensions = ['pdf', 'doc', 'docx'];
    
    if (!allowedTypes.includes(file.type) && !allowedExtensions.includes(fileExtension)) {
        alert('Please select a PDF, DOC, or DOCX file. Selected file type: ' + file.type);
        // Clear the file input
        fileInput.value = '';
        return;
    }
    
    // Validate file size (10MB limit)
    const maxSize = 10 * 1024 * 1024; // 10MB in bytes
    if (file.size > maxSize) {
        alert('File size must be less than 10MB. Selected file size: ' + (file.size / 1024 / 1024).toFixed(2) + 'MB');
        // Clear the file input
        fileInput.value = '';
        return;
    }
    
    console.log('File validation passed, updating UI');
    
    // Update UI
    uploadArea.style.display = 'none';
    fileInfo.style.display = 'block';
    fileName.textContent = file.name;
    
    // Create a new file list with the selected file and assign it to the input
    const dt = new DataTransfer();
    dt.items.add(file);
    fileInput.files = dt.files;
    
    console.log('File input updated:', fileInput.files.length);
    
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
            // Reset submit button first
            resetSubmitButton();
            
            // Show success modal
            const successModal = new bootstrap.Modal(document.getElementById('successModal'));
            const modalElement = document.getElementById('successModal');
            
            // Function to handle modal close
            function closeModalAndTab() {
                successModal.hide();
                setTimeout(() => {
                    try {
                        window.close();
                    } catch (e) {
                        // If window.close() fails, redirect or show a message
                        window.location.href = 'about:blank';
                    }
                }, 300);
            }
            
            // Add event listeners to close tab when modal is dismissed
            modalElement.addEventListener('hidden.bs.modal', function() {
                try {
                    window.close();
                } catch (e) {
                    window.location.href = 'about:blank';
                }
            });
            
            // Handle close button clicks
            const closeButtons = modalElement.querySelectorAll('[data-bs-dismiss="modal"], .btn-close, .btn-custom');
            closeButtons.forEach(button => {
                button.addEventListener('click', closeModalAndTab);
            });
            
            // Also close tab when clicking outside modal
            modalElement.addEventListener('click', function(e) {
                if (e.target === modalElement) {
                    closeModalAndTab();
                }
            });
            
            // Handle Escape key
            document.addEventListener('keydown', function(e) {
                if (e.key === 'Escape' && modalElement.classList.contains('show')) {
                    closeModalAndTab();
                }
            });
            
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