document.addEventListener('DOMContentLoaded', () => {
    // UI Elements
    const dragDropArea = document.getElementById('drag-drop-area');
    const fileInput = document.getElementById('file-input');
    const fileInfo = document.getElementById('file-info');
    const fileName = document.getElementById('file-name');
    const fileSize = document.getElementById('file-size');
    const removeBtn = document.getElementById('remove-btn');
    const processBtn = document.getElementById('process-btn');
    
    // Cards
    const uploadCard = document.getElementById('upload-card');
    const statusCard = document.getElementById('status-card');
    const resultCard = document.getElementById('result-card');
    
    // Status Elements
    const statusText = document.getElementById('status-text');
    const subStatusText = document.getElementById('sub-status-text');
    const step1 = document.getElementById('step-1');
    const step2 = document.getElementById('step-2');
    const step3 = document.getElementById('step-3');
    const connectors = document.querySelectorAll('.step-connector');
    
    // Result Elements
    const resultContent = document.getElementById('result-content');
    const transcriptContent = document.getElementById('transcript-content');
    const copyBtn = document.getElementById('copy-btn');
    const resetBtn = document.getElementById('reset-btn');
    const toast = document.getElementById('toast');
    
    // Tab Interaction
    const tabBtns = document.querySelectorAll('.tab-btn');
    const tabContents = document.querySelectorAll('.tab-content');

    let selectedFile = null;
    let pollInterval = null;

    // --- Tab Switching Logic ---
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const tabName = btn.getAttribute('data-tab');
            
            // Toggle active button
            tabBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            // Toggle active content
            tabContents.forEach(content => {
                if (content.id === `${tabName}-section`) {
                    content.classList.remove('hidden');
                } else {
                    content.classList.add('hidden');
                }
            });
        });
    });

    // --- File Drag & Drop Handlers ---
    dragDropArea.addEventListener('click', () => fileInput.click());

    dragDropArea.addEventListener('dragover', (e) => {
        e.preventDefault();
        dragDropArea.classList.add('dragover');
    });

    dragDropArea.addEventListener('dragleave', () => {
        dragDropArea.classList.remove('dragover');
    });

    dragDropArea.addEventListener('drop', (e) => {
        e.preventDefault();
        dragDropArea.classList.remove('dragover');
        if (e.dataTransfer.files.length) {
            handleFileSelection(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length) {
            handleFileSelection(fileInput.files[0]);
        }
    });

    // --- File Selection Logic ---
    function handleFileSelection(file) {
        // Basic validation
        if (!file.name.toLowerCase().endsWith('.mp3') && !file.name.toLowerCase().endsWith('.wav')) {
            showToast('Please select a valid .mp3 or .wav file', 'error');
            return;
        }

        selectedFile = file;
        fileName.textContent = file.name;
        fileSize.textContent = formatBytes(file.size);
        
        dragDropArea.classList.add('hidden');
        fileInfo.classList.remove('hidden');
        processBtn.classList.remove('disabled');
    }

    removeBtn.addEventListener('click', () => {
        resetUploadState();
    });

    function resetUploadState() {
        selectedFile = null;
        fileInput.value = '';
        dragDropArea.classList.remove('hidden');
        fileInfo.classList.add('hidden');
        processBtn.classList.add('disabled');
    }

    // --- API Interactions ---
    processBtn.addEventListener('click', async () => {
        if (!selectedFile || processBtn.classList.contains('disabled')) return;

        // Switch UI to Processing State
        uploadCard.classList.add('hidden');
        statusCard.classList.remove('hidden');
        
        try {
            // 1. Upload to S3 Endpoint
            const formData = new FormData();
            formData.append('audio_file', selectedFile);

            const uploadResponse = await fetch('/upload', {
                method: 'POST',
                body: formData
            });

            const uploadResult = await uploadResponse.json();

            if (!uploadResponse.ok) {
                throw new Error(uploadResult.error || 'Upload failed');
            }

            // Upload Success - Update UI Steps
            step1.classList.remove('active');
            step1.classList.add('completed');
            connectors[0].classList.add('completed');
            step2.classList.add('active');
            
            statusText.textContent = 'Transcribing audio...';
            subStatusText.textContent = 'Amazon Transcribe is processing the file (this takes a few minutes)';

            if (uploadResult.transcript) {
                // If backend already returned transcript, skip polling
                step2.classList.remove('active');
                step2.classList.add('completed');
                statusText.textContent = 'Completed';
                subStatusText.textContent = 'Transcript ready';
                showResults(uploadResult.transcript);
            } else {
                // 2. Start Polling for Results
                startPolling(uploadResult.filename, uploadResult.upload_time);
            }

        } catch (error) {
            showToast(error.message, 'error');
            resetUploadState();
            uploadCard.classList.remove('hidden');
            statusCard.classList.add('hidden');
        }
    });

    function startPolling(filename, uploadTime) {
        let pollCount = 0;
        
        pollInterval = setInterval(async () => {
            pollCount++;
            
            try {
                const response = await fetch(`/check_status?filename=${encodeURIComponent(filename)}&upload_time=${uploadTime}`);
                const result = await response.json();

                if (result.status === 'completed') {
                    // Success! Pipeline finished.
                    clearInterval(pollInterval);
                    showResults(result.summary, result.transcript);
                } else if (result.transcript_ready) {
                    // Transcript is ready, but summary is still processing
                    step2.classList.remove('active');
                    step2.classList.add('completed');
                    statusText.textContent = 'Completed';
                    subStatusText.textContent = 'Transcript available';
                }
                
            } catch (error) {
                console.error("Polling error:", error);
            }
        }, 5000); // Check every 5 seconds
    }

    // --- Show Results ---
    function showResults(fullTranscript) {
        statusCard.classList.add('hidden');
        resultCard.classList.remove('hidden');

        // show only transcription
        transcriptContent.textContent = fullTranscript;

        showToast('Pipeline completed successfully!', 'success');
    }

    // --- Utilities ---
    copyBtn.addEventListener('click', () => {
        navigator.clipboard.writeText(resultContent.innerText).then(() => {
            copyBtn.innerHTML = '<i class="fa-solid fa-check"></i>';
            setTimeout(() => {
                copyBtn.innerHTML = '<i class="fa-regular fa-copy"></i>';
            }, 2000);
            showToast('Copied to clipboard!', 'success');
        });
    });

    resetBtn.addEventListener('click', () => {
        resultCard.classList.add('hidden');
        uploadCard.classList.remove('hidden');
        resetUploadState();
        
        // Reset Steps UI
        step1.classList.add('active');
        step1.classList.remove('completed');
        step2.classList.remove('active', 'completed');
        step3.classList.remove('active', 'completed');
        connectors.forEach(c => c.classList.remove('completed'));
        
        statusText.textContent = 'Uploading to S3...';
        subStatusText.textContent = 'Waking up the pipeline';
    });

    function formatBytes(bytes, decimals = 1) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const dm = decimals < 0 ? 0 : decimals;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
    }

    function showToast(message, type = 'success') {
        toast.textContent = message;
        toast.className = `toast show ${type}`;
        
        setTimeout(() => {
            toast.classList.remove('show');
        }, 4000);
    }
});
