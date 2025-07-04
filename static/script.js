document.addEventListener('DOMContentLoaded', function() {
    const uploadArea = document.getElementById('uploadArea');
    const fileInput = document.getElementById('fileInput');
    const fileInfo = document.getElementById('fileInfo');
    const fileName = document.getElementById('fileName');
    const fileSize = document.getElementById('fileSize');
    const submitBtn = document.getElementById('submitBtn');
    const uploadForm = document.getElementById('uploadForm');
    const progressContainer = document.getElementById('progressContainer');
    const progressBar = document.getElementById('progressBar');
    const validationResult = document.getElementById('validationResult');
    const validationMessage = document.getElementById('validationMessage');

    // File upload area click handler
    uploadArea.addEventListener('click', function() {
        fileInput.click();
    });

    // Drag and drop handlers
    uploadArea.addEventListener('dragover', function(e) {
        e.preventDefault();
        uploadArea.classList.add('drag-over');
    });

    uploadArea.addEventListener('dragleave', function(e) {
        e.preventDefault();
        uploadArea.classList.remove('drag-over');
    });

    uploadArea.addEventListener('drop', function(e) {
        e.preventDefault();
        uploadArea.classList.remove('drag-over');
        
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            const file = files[0];
            if (file.type === 'text/xml' || file.name.endsWith('.xml')) {
                fileInput.files = files;
                handleFileSelection(file);
            } else {
                alert('Please select an XML file');
            }
        }
    });

    // File input change handler
    fileInput.addEventListener('change', function(e) {
        const file = e.target.files[0];
        if (file) {
            handleFileSelection(file);
        }
    });

    // Handle file selection
    function handleFileSelection(file) {
        // Display file info
        fileName.textContent = file.name;
        fileSize.textContent = formatFileSize(file.size);
        fileInfo.style.display = 'block';
        
        // Validate file
        validateFile(file);
    }

    // Validate file function
    function validateFile(file) {
        const formData = new FormData();
        formData.append('file', file);
        
        fetch('/validate', {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.valid) {
                validationMessage.innerHTML = `Valid XML file with ${data.job_count} jobs found`;
                validationResult.style.display = 'block';
                submitBtn.disabled = false;
                
                // Update validation icon
                const icon = validationResult.querySelector('i');
                icon.className = 'fas fa-check-circle text-success me-1';
            } else {
                validationMessage.innerHTML = `Invalid XML file: ${data.error}`;
                validationResult.style.display = 'block';
                submitBtn.disabled = true;
                
                // Update validation icon
                const icon = validationResult.querySelector('i');
                icon.className = 'fas fa-exclamation-triangle text-danger me-1';
            }
        })
        .catch(error => {
            console.error('Validation error:', error);
            validationMessage.innerHTML = 'Error validating file';
            validationResult.style.display = 'block';
            submitBtn.disabled = true;
            
            // Update validation icon
            const icon = validationResult.querySelector('i');
            icon.className = 'fas fa-exclamation-triangle text-danger me-1';
        });
    }

    // Form submission handler
    uploadForm.addEventListener('submit', function(e) {
        // Show progress
        progressContainer.style.display = 'block';
        submitBtn.disabled = true;
        
        // Start progress animation
        let progress = 0;
        const progressInterval = setInterval(() => {
            progress += Math.random() * 15;
            if (progress >= 90) {
                progress = 90;
                clearInterval(progressInterval);
            }
            progressBar.style.width = progress + '%';
        }, 500);
        
        // The form will submit normally, no need to prevent default
        // Progress will be completed when page reloads
    });

    // Format file size function
    function formatFileSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }

    // Auto-hide alerts after 5 seconds
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            const bsAlert = new bootstrap.Alert(alert);
            bsAlert.close();
        }, 5000);
    });
});
