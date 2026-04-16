document.addEventListener('DOMContentLoaded', function() {
    const audioFileInput = document.getElementById('audioFile');
    const fileNameDisplay = document.getElementById('fileNameDisplay');
    const submitBtn = document.getElementById('submitBtn');
    const statusMessage = document.getElementById('statusMessage');
    const transcriptionResult = document.getElementById('transcriptionResult');
    const copyBtn = document.getElementById('copyBtn');
    const recalibrateBtn = document.getElementById('recalibrateBtn');
    const summarizeBtn = document.getElementById('summarizeBtn');
    const generateNotesBtn = document.getElementById('generateNotesBtn');
    const progressBarContainer = document.getElementById('progressBarContainer');
    const progressBar = document.getElementById('progressBar');
    const progressText = document.getElementById('progressText');
    const progressPercent = document.getElementById('progressPercent');

    let currentRawTranscription = null;
    let currentCalibratedText = null;
    let summaryText = null;
    let isShowingSummary = false;
    let notesText = null;
    let isShowingNotes = false;
    let activeController = null;
    let activeRequestId = 0;

    function updateStatus(message, type) {
        statusMessage.textContent = message || '';
        statusMessage.classList.remove('error', 'success', 'info', 'hidden');
        if (type) {
            statusMessage.classList.add(type);
        } else {
            statusMessage.classList.add('hidden');
        }
        if (message && type) {
            statusMessage.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }

    function setActionButtonsDisabledState(disabled) {
        const hasContent = currentCalibratedText && currentCalibratedText.trim() !== '';
        submitBtn.disabled = disabled || !audioFileInput.files[0];
        recalibrateBtn.disabled = disabled || !hasContent;
        summarizeBtn.disabled = disabled || !hasContent;
        generateNotesBtn.disabled = disabled || !hasContent;
        copyBtn.disabled = disabled || transcriptionResult.textContent.trim() === '';
    }

    function showProgressBar() {
        progressBarContainer.classList.remove('hidden');
        progressBar.style.width = '0%';
        progressText.textContent = '准备中...';
        progressPercent.textContent = '0%';
        progressBar.classList.add('pulsing');
    }

    function updateProgress(progress, message, data) {
        progressBar.style.width = `${progress}%`;
        progressText.textContent = message;
        if (data && Number.isInteger(data.completed_chunks) && Number.isInteger(data.total_chunks)) {
            progressPercent.textContent = `${progress}% (${data.completed_chunks}/${data.total_chunks})`;
        } else {
            progressPercent.textContent = `${progress}%`;
        }
        if (progress >= 95) {
            progressBar.classList.remove('pulsing');
        }
    }

    function hideProgressBar() {
        progressBarContainer.classList.add('hidden');
        progressBar.classList.remove('pulsing');
    }

    function abortActiveRequest(silent) {
        if (!activeController) {
            return;
        }
        activeController.abort();
        activeController = null;
        if (!silent) {
            hideProgressBar();
            updateStatus('当前转录任务已取消。', 'info');
        }
    }

    function resetGeneratedViews() {
        summaryText = null;
        isShowingSummary = false;
        summarizeBtn.textContent = '量子速读';
        notesText = null;
        isShowingNotes = false;
        generateNotesBtn.textContent = '生成笔记';
        transcriptionResult.textContent = currentCalibratedText || '';
    }

    function resetResultState() {
        currentRawTranscription = null;
        currentCalibratedText = null;
        resetGeneratedViews();
        copyBtn.textContent = '复制文本';
        copyBtn.classList.remove('copied-success', 'copied-error');
    }

    function handleSuccess(data, operationType) {
        if (data.raw_transcription) {
            currentRawTranscription = data.raw_transcription;
        }
        currentCalibratedText = data.transcription;
        resetGeneratedViews();
        updateStatus(data.calibration_message || `${operationType}完成。`, data.is_calibrated ? 'success' : 'info');
        setActionButtonsDisabledState(false);
    }

    function parseSSEChunk(buffer, onEvent) {
        const events = buffer.split('\n\n');
        const remaining = events.pop() || '';
        for (const eventText of events) {
            const lines = eventText.split('\n');
            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        onEvent(JSON.parse(line.slice(6).trim()));
                    } catch (error) {
                        console.error('Failed to parse SSE data:', error, line);
                    }
                }
            }
        }
        return remaining;
    }

    function extractSSEErrorMessage(text) {
        let errorMessage = null;
        parseSSEChunk(text, (eventData) => {
            if (!errorMessage && eventData && eventData.message) {
                errorMessage = eventData.message;
            }
        });
        return errorMessage;
    }

    function handleProgressEvent(eventData, submitBtnSpan, originalText) {
        const { stage, message, progress, data } = eventData;
        updateProgress(progress, message, data);

        if (stage === 'DONE') {
            hideProgressBar();
            if (data) {
                handleSuccess(data, '转录');
            }
            submitBtnSpan.textContent = originalText;
            return;
        }

        if (stage === 'ERROR') {
            hideProgressBar();
            currentCalibratedText = null;
            updateStatus(`发生错误: ${message}`, 'error');
            submitBtnSpan.textContent = originalText;
            setActionButtonsDisabledState(false);
            return;
        }

        updateStatus(message, 'info');
    }

    async function submitTranscription(file) {
        const requestId = ++activeRequestId;
        const controller = new AbortController();
        activeController = controller;

        showProgressBar();
        updateStatus('正在上传和转录音频...', 'info');
        setActionButtonsDisabledState(true);

        const submitBtnSpan = submitBtn.querySelector('span') || submitBtn;
        const originalText = submitBtnSpan.textContent;
        submitBtnSpan.textContent = '处理中...';

        const formData = new FormData();
        formData.append('audio_file', file);

        try {
            const response = await fetch('/api/transcribe-stream', {
                method: 'POST',
                body: formData,
                signal: controller.signal,
            });

            if (!response.ok) {
                const errorText = await response.text();
                const contentType = response.headers.get('content-type') || '';
                if (contentType.includes('text/event-stream')) {
                    const sseMessage = extractSSEErrorMessage(errorText);
                    throw new Error(sseMessage || `请求失败 (状态 ${response.status})`);
                }
                throw new Error(errorText || `HTTP ${response.status}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) {
                    break;
                }
                buffer += decoder.decode(value, { stream: true });
                buffer = parseSSEChunk(buffer, (eventData) => {
                    if (activeRequestId === requestId) {
                        handleProgressEvent(eventData, submitBtnSpan, originalText);
                    }
                });
            }
        } catch (error) {
            if (error.name === 'AbortError') {
                if (activeRequestId === requestId) {
                    hideProgressBar();
                    updateStatus('当前转录任务已取消。', 'info');
                }
                return;
            }
            console.error('转录错误:', error);
            if (activeRequestId === requestId) {
                hideProgressBar();
                updateStatus(`发生错误: ${error.message}`, 'error');
                currentCalibratedText = null;
            }
        } finally {
            if (activeRequestId === requestId) {
                activeController = null;
                submitBtnSpan.textContent = originalText;
                setActionButtonsDisabledState(false);
            }
        }
    }

    audioFileInput.addEventListener('change', function(event) {
        const file = event.target.files[0];
        abortActiveRequest(true);
        updateStatus(null, null);
        hideProgressBar();
        resetResultState();

        if (file) {
            fileNameDisplay.textContent = file.name;
        } else {
            fileNameDisplay.textContent = '未选择文件';
        }

        setActionButtonsDisabledState(false);
    });

    submitBtn.addEventListener('click', function(event) {
        event.preventDefault();
        const file = audioFileInput.files[0];
        if (!file) {
            updateStatus('请先选择一个音频文件。', 'error');
            return;
        }
        abortActiveRequest(true);
        submitTranscription(file);
    });

    recalibrateBtn.addEventListener('click', async function() {
        if (!currentRawTranscription) {
            updateStatus('没有可供重新校准的原始转录文本。', 'info');
            return;
        }

        updateStatus('正在重新校准...', 'info');
        setActionButtonsDisabledState(true);
        const originalText = recalibrateBtn.textContent;
        recalibrateBtn.textContent = '校准中...';

        try {
            const response = await fetch('/api/recalibrate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ raw_transcription: currentRawTranscription }),
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || `请求失败 (状态 ${response.status})`);
            }
            handleSuccess(data, '重新校准');
        } catch (error) {
            console.error('重新校准错误:', error);
            updateStatus(`重新校准时发生错误: ${error.message}`, 'error');
        } finally {
            recalibrateBtn.textContent = originalText;
            setActionButtonsDisabledState(false);
        }
    });

    summarizeBtn.addEventListener('click', async function() {
        if (summaryText) {
            isShowingSummary = !isShowingSummary;
            transcriptionResult.textContent = isShowingSummary ? summaryText : currentCalibratedText;
            summarizeBtn.textContent = isShowingSummary ? '显示原文' : '显示摘要';
            copyBtn.disabled = transcriptionResult.textContent.trim() === '';
            return;
        }

        if (!currentCalibratedText || currentCalibratedText.trim() === '') {
            updateStatus('没有可供总结的文本。', 'info');
            return;
        }

        updateStatus('正在生成摘要...', 'info');
        setActionButtonsDisabledState(true);
        const originalText = summarizeBtn.textContent;
        summarizeBtn.textContent = '生成中...';

        try {
            const response = await fetch('/api/summarize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text_to_summarize: currentCalibratedText }),
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || `请求失败 (状态 ${response.status})`);
            }
            summaryText = data.summary;
            isShowingSummary = true;
            transcriptionResult.textContent = summaryText;
            updateStatus('摘要生成成功！', 'success');
            summarizeBtn.textContent = '显示原文';
        } catch (error) {
            console.error('生成摘要错误:', error);
            updateStatus(`生成摘要失败 (${error.message})，请重试...`, 'error');
            summarizeBtn.textContent = originalText;
        } finally {
            setActionButtonsDisabledState(false);
        }
    });

    generateNotesBtn.addEventListener('click', async function() {
        if (notesText) {
            isShowingNotes = !isShowingNotes;
            transcriptionResult.textContent = isShowingNotes ? notesText : currentCalibratedText;
            generateNotesBtn.textContent = isShowingNotes ? '显示原文' : '显示笔记';
            copyBtn.disabled = transcriptionResult.textContent.trim() === '';
            return;
        }

        if (!currentCalibratedText || currentCalibratedText.trim() === '') {
            updateStatus('没有可供生成笔记的文本。', 'info');
            return;
        }

        updateStatus('正在生成笔记...', 'info');
        setActionButtonsDisabledState(true);
        const originalText = generateNotesBtn.textContent;
        generateNotesBtn.textContent = '生成中...';

        try {
            const response = await fetch('/api/generatenote', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text_to_process: currentCalibratedText }),
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || `请求失败 (状态 ${response.status})`);
            }
            notesText = data.notes;
            isShowingNotes = true;
            transcriptionResult.textContent = notesText;
            updateStatus('笔记生成成功！', 'success');
            generateNotesBtn.textContent = '显示原文';
        } catch (error) {
            console.error('生成笔记错误:', error);
            updateStatus(`生成笔记失败 (${error.message})，请重试...`, 'error');
            generateNotesBtn.textContent = originalText;
        } finally {
            setActionButtonsDisabledState(false);
        }
    });

    copyBtn.addEventListener('click', function() {
        if (copyBtn.disabled) {
            return;
        }
        const textToCopy = transcriptionResult.textContent;
        if (!textToCopy || textToCopy.trim() === '') {
            updateStatus('没有可复制的文本。', 'info');
            return;
        }

        copyBtn.classList.remove('copied-success', 'copied-error');
        navigator.clipboard.writeText(textToCopy).then(function() {
            copyBtn.textContent = '已复制!';
            copyBtn.classList.add('copied-success');
            setTimeout(function() {
                copyBtn.textContent = '复制文本';
                copyBtn.classList.remove('copied-success');
            }, 2000);
        }).catch(function(error) {
            console.error('无法复制文本:', error);
            copyBtn.textContent = '复制失败';
            copyBtn.classList.add('copied-error');
            setTimeout(function() {
                copyBtn.textContent = '复制文本';
                copyBtn.classList.remove('copied-error');
            }, 3000);
        });
    });

    window.addEventListener('beforeunload', function() {
        abortActiveRequest(true);
    });

    window.addEventListener('pagehide', function() {
        abortActiveRequest(true);
    });

    setActionButtonsDisabledState(false);
    hideProgressBar();
});
