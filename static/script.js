document.addEventListener('DOMContentLoaded', function() {
    const audioFileInput = document.getElementById('audioFile');
    const audioFolderInput = document.getElementById('audioFolder');
    const uploadDropzone = document.getElementById('uploadDropzone');
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
    const taskList = document.getElementById('taskList');
    const taskEmptyState = document.getElementById('taskEmptyState');
    const queueSummary = document.getElementById('queueSummary');
    const resultTitle = document.getElementById('resultTitle');
    const exportMarkdownBtn = document.getElementById('exportMarkdownBtn');

    let tasks = [];
    let selectedTaskId = null;
    let isBatchRunning = false;
    let nextTaskId = 1;
    const activeControllers = new Map();
    const AUDIO_EXTENSIONS = new Set([
        '.aac', '.aiff', '.amr', '.flac', '.m4a', '.m4b', '.mid', '.midi', '.mp3',
        '.oga', '.ogg', '.opus', '.ra', '.wav', '.weba', '.webm', '.wma',
    ]);

    function updateStatus(message, type) {
        statusMessage.textContent = message || '';
        statusMessage.classList.remove('error', 'success', 'info', 'hidden');
        if (type) {
            statusMessage.classList.add(type);
        } else {
            statusMessage.classList.add('hidden');
        }
        if (message && (type === 'success' || type === 'error')) {
            statusMessage.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }

    function createTask(file) {
        return {
            id: nextTaskId++,
            file,
            status: 'pending',
            stage: 'PENDING',
            message: '等待开始',
            progress: 0,
            progressData: null,
            rawTranscription: null,
            transcription: null,
            calibrationMessage: null,
            isCalibrated: false,
            summaryText: null,
            isShowingSummary: false,
            notesText: null,
            isShowingNotes: false,
            errorMessage: null,
            requestId: 0,
        };
    }

    function getTaskById(taskId) {
        return tasks.find((task) => task.id === taskId) || null;
    }

    function getSelectedTask() {
        return getTaskById(selectedTaskId);
    }

    function hasActiveTasks() {
        return tasks.some((task) => ['uploading', 'queued', 'processing'].includes(task.status));
    }

    function hasRunnableTasks() {
        return tasks.some((task) => ['pending', 'error', 'cancelled'].includes(task.status));
    }

    function fileIdentity(file) {
        return [file.name, file.size, file.lastModified].join('::');
    }

    function isAudioLikeFile(file) {
        if (!file) {
            return false;
        }
        if (file.type && file.type.startsWith('audio/')) {
            return true;
        }
        const fileName = String(file.name || '');
        const dotIndex = fileName.lastIndexOf('.');
        const extension = dotIndex >= 0 ? fileName.slice(dotIndex).toLowerCase() : '';
        return AUDIO_EXTENSIONS.has(extension);
    }

    function getAudioFilesFromSelection(fileList) {
        return Array.from(fileList || []).filter(isAudioLikeFile);
    }

    function resetGeneratedViews(task) {
        task.summaryText = null;
        task.isShowingSummary = false;
        task.notesText = null;
        task.isShowingNotes = false;
    }

    function getVisibleTaskText(task) {
        if (!task) {
            return '';
        }
        if (task.isShowingNotes && task.notesText) {
            return task.notesText;
        }
        if (task.isShowingSummary && task.summaryText) {
            return task.summaryText;
        }
        return task.transcription || '';
    }

    function canExportMarkdown() {
        return tasks.some((task) => task.status === 'success' && task.transcription && task.transcription.trim());
    }

    function sanitizeMarkdownText(text) {
        return (text || '').replace(/\r\n/g, '\n').trim();
    }

    function sanitizeFilenamePart(text) {
        return (text || 'task')
            .replace(/[\\/:*?"<>|]+/g, '-')
            .replace(/\s+/g, ' ')
            .trim();
    }

    function buildMarkdownExport() {
        const lines = [
            '# 批量转录结果',
            '',
            `生成时间：${new Date().toLocaleString('zh-CN', { hour12: false })}`,
            `任务总数：${tasks.length}`,
            '',
        ];

        tasks.forEach((task, index) => {
            lines.push(`## ${index + 1}. ${task.file.name}`);
            lines.push('');
            lines.push(`- 状态：${getTaskStatusLabel(task)}`);
            if (task.calibrationMessage) {
                lines.push(`- 校准信息：${task.calibrationMessage}`);
            }
            if (task.errorMessage) {
                lines.push(`- 错误信息：${task.errorMessage}`);
            }
            lines.push('');

            if (task.transcription && task.transcription.trim()) {
                lines.push('### 转录文本');
                lines.push('');
                lines.push(sanitizeMarkdownText(task.transcription));
                lines.push('');
            }

            if (task.summaryText && task.summaryText.trim()) {
                lines.push('### 摘要');
                lines.push('');
                lines.push(sanitizeMarkdownText(task.summaryText));
                lines.push('');
            }

            if (task.notesText && task.notesText.trim()) {
                lines.push('### 笔记');
                lines.push('');
                lines.push(sanitizeMarkdownText(task.notesText));
                lines.push('');
            }
        });

        return `${lines.join('\n').trim()}\n`;
    }

    function exportMarkdown() {
        if (!canExportMarkdown()) {
            updateStatus('当前没有可导出的已完成转录结果。', 'info');
            return;
        }

        const markdown = buildMarkdownExport();
        const now = new Date();
        const timestamp = [
            now.getFullYear(),
            String(now.getMonth() + 1).padStart(2, '0'),
            String(now.getDate()).padStart(2, '0'),
            '-',
            String(now.getHours()).padStart(2, '0'),
            String(now.getMinutes()).padStart(2, '0'),
            String(now.getSeconds()).padStart(2, '0'),
        ].join('');
        const firstTaskName = tasks.length ? sanitizeFilenamePart(tasks[0].file.name.replace(/\.[^.]+$/, '')) : 'batch';
        const filename = `speech-to-text-${firstTaskName}-${timestamp}.md`;
        const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        updateStatus(`已导出 Markdown：${filename}`, 'success');
    }

    function syncActionButtonLabels() {
        const task = getSelectedTask();
        if (!task) {
            summarizeBtn.textContent = '量子速读';
            generateNotesBtn.textContent = '生成笔记';
            return;
        }
        if (task.summaryText) {
            summarizeBtn.textContent = task.isShowingSummary ? '显示原文' : '显示摘要';
        } else {
            summarizeBtn.textContent = '量子速读';
        }
        if (task.notesText) {
            generateNotesBtn.textContent = task.isShowingNotes ? '显示原文' : '显示笔记';
        } else {
            generateNotesBtn.textContent = '生成笔记';
        }
    }

    function getTaskStatusLabel(task) {
        if (task.status === 'pending') {
            return '待开始';
        }
        if (task.status === 'uploading') {
            return '上传中';
        }
        if (task.status === 'queued') {
            return '排队中';
        }
        if (task.status === 'processing') {
            return '处理中';
        }
        if (task.status === 'success') {
            return '已完成';
        }
        if (task.status === 'cancelled') {
            return '已取消';
        }
        return '失败';
    }

    function updateOverallProgress() {
        const activeCount = tasks.filter((task) => ['uploading', 'queued', 'processing'].includes(task.status)).length;
        const finishedCount = tasks.filter((task) => ['success', 'error', 'cancelled'].includes(task.status)).length;

        if (!tasks.length || (!activeCount && !isBatchRunning)) {
            progressBarContainer.classList.add('hidden');
            progressBar.classList.remove('pulsing');
            return;
        }

        const totalProgress = tasks.reduce((sum, task) => sum + task.progress, 0);
        const averageProgress = tasks.length ? Math.round(totalProgress / tasks.length) : 0;
        progressBarContainer.classList.remove('hidden');
        progressBar.style.width = `${averageProgress}%`;
        progressText.textContent = `批量处理中：已完成 ${finishedCount}/${tasks.length}，运行中 ${activeCount} 个`;
        progressPercent.textContent = `${averageProgress}%`;
        if (activeCount > 0 && averageProgress < 95) {
            progressBar.classList.add('pulsing');
        } else {
            progressBar.classList.remove('pulsing');
        }
    }

    function updateResultPanel() {
        const task = getSelectedTask();
        resultTitle.textContent = task ? `转录结果 · ${task.file.name}` : '转录结果';
        transcriptionResult.textContent = getVisibleTaskText(task);
        syncActionButtonLabels();

        if (!task) {
            updateStatus(null, null);
            return;
        }

        if (task.status === 'error') {
            updateStatus(`文件 ${task.file.name} 处理失败: ${task.errorMessage || task.message}`, 'error');
            return;
        }
        if (task.status === 'cancelled') {
            updateStatus(`文件 ${task.file.name} 的任务已取消。`, 'info');
            return;
        }
        if (task.status === 'success') {
            updateStatus(
                task.calibrationMessage || `文件 ${task.file.name} 处理完成。`,
                task.isCalibrated ? 'success' : 'info'
            );
            return;
        }
        if (task.status !== 'pending') {
            updateStatus(`文件 ${task.file.name}: ${task.message}`, 'info');
            return;
        }
        updateStatus(`已选择文件 ${task.file.name}，等待开始处理。`, 'info');
    }

    function setActionButtonsDisabledState() {
        const task = getSelectedTask();
        const hasContent = Boolean(task && task.transcription && task.transcription.trim() !== '');
        const selectedTaskBusy = Boolean(task && ['uploading', 'queued', 'processing'].includes(task.status));

        submitBtn.disabled = !hasRunnableTasks();
        recalibrateBtn.disabled = !hasContent || selectedTaskBusy;
        summarizeBtn.disabled = !hasContent || selectedTaskBusy;
        generateNotesBtn.disabled = !hasContent || selectedTaskBusy;
        copyBtn.disabled = !getVisibleTaskText(task).trim();
        exportMarkdownBtn.disabled = !canExportMarkdown();
    }

    function setSelectedTask(taskId) {
        selectedTaskId = taskId;
        renderTaskList();
        updateResultPanel();
        setActionButtonsDisabledState();
    }

    function renderTaskList() {
        taskList.innerHTML = '';
        taskEmptyState.classList.toggle('hidden', tasks.length > 0);
        if (!tasks.length) {
            taskList.appendChild(taskEmptyState);
            queueSummary.textContent = '选择多个音频后可并行转录，并分别查看结果。';
            return;
        }

        const successCount = tasks.filter((task) => task.status === 'success').length;
        const errorCount = tasks.filter((task) => task.status === 'error').length;
        const activeCount = tasks.filter((task) => ['uploading', 'queued', 'processing'].includes(task.status)).length;
        queueSummary.textContent = `共 ${tasks.length} 个文件，已完成 ${successCount} 个，失败 ${errorCount} 个，处理中 ${activeCount} 个。`;

        tasks.forEach((task) => {
            const item = document.createElement('div');
            item.className = 'task-item';
            if (task.id === selectedTaskId) {
                item.classList.add('selected');
            }
            item.addEventListener('click', function() {
                setSelectedTask(task.id);
            });

            const header = document.createElement('div');
            header.className = 'task-item-header';

            const name = document.createElement('div');
            name.className = 'task-item-name';
            name.textContent = task.file.name;
            header.appendChild(name);

            const status = document.createElement('span');
            status.className = `task-item-status ${task.status}`;
            status.textContent = getTaskStatusLabel(task);
            header.appendChild(status);
            item.appendChild(header);

            const message = document.createElement('div');
            message.className = 'task-item-message';
            message.textContent = task.message;
            item.appendChild(message);

            const progressTrack = document.createElement('div');
            progressTrack.className = 'task-item-progress-track';
            const progressFill = document.createElement('div');
            progressFill.className = 'task-item-progress-fill';
            progressFill.style.width = `${task.progress}%`;
            progressTrack.appendChild(progressFill);
            item.appendChild(progressTrack);

            const meta = document.createElement('div');
            meta.className = 'task-item-meta';

            const leftMeta = document.createElement('span');
            if (
                task.progressData &&
                Number.isInteger(task.progressData.completed_chunks) &&
                Number.isInteger(task.progressData.total_chunks)
            ) {
                leftMeta.textContent = `分块进度 ${task.progressData.completed_chunks}/${task.progressData.total_chunks}`;
            } else {
                leftMeta.textContent = task.status === 'success'
                    ? (task.isCalibrated ? '校准已完成' : '已返回结果')
                    : task.status === 'error'
                        ? '处理失败'
                        : '等待结果';
            }

            const rightMeta = document.createElement('span');
            rightMeta.textContent = `${task.progress}%`;
            meta.appendChild(leftMeta);
            meta.appendChild(rightMeta);
            item.appendChild(meta);

            taskList.appendChild(item);
        });
    }

    function updateFileNameDisplay(files) {
        if (!files.length) {
            fileNameDisplay.textContent = '未选择文件，可重复点击追加';
            return;
        }
        const preview = files.slice(0, 3).map((file) => file.name).join('、');
        if (files.length <= 3) {
            fileNameDisplay.textContent = `已加入 ${files.length} 个文件：${preview}`;
            return;
        }
        fileNameDisplay.textContent = `已加入 ${files.length} 个文件：${preview} 等`;
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

    function clearTaskResult(task) {
        task.rawTranscription = null;
        task.transcription = null;
        task.calibrationMessage = null;
        task.isCalibrated = false;
        task.errorMessage = null;
        resetGeneratedViews(task);
    }

    function handleTaskProgressEvent(task, eventData) {
        const { stage, message, progress, data } = eventData;
        task.stage = stage;
        task.message = message;
        task.progress = progress;
        task.progressData = data || null;

        if (stage === 'QUEUED' || stage === 'WAITING_FOR_S2T_SLOT' || stage === 'WAITING_FOR_LLM_SLOT') {
            task.status = 'queued';
        } else if (stage === 'DONE') {
            task.status = 'success';
            if (data) {
                task.rawTranscription = data.raw_transcription || null;
                task.transcription = data.transcription || null;
                task.calibrationMessage = data.calibration_message || null;
                task.isCalibrated = Boolean(data.is_calibrated);
                task.errorMessage = null;
                resetGeneratedViews(task);
            }
            task.message = task.calibrationMessage || '处理完成';
        } else if (stage === 'ERROR') {
            task.status = 'error';
            task.errorMessage = message;
            task.message = message;
        } else if (stage !== 'STARTING') {
            task.status = 'processing';
        } else {
            task.status = 'uploading';
        }

        renderTaskList();
        updateOverallProgress();
        if (task.id === selectedTaskId) {
            updateResultPanel();
            setActionButtonsDisabledState();
        }
    }

    function isCurrentTaskRequest(task, requestId) {
        return getTaskById(task.id) === task && task.requestId === requestId;
    }

    async function submitTaskTranscription(task) {
        task.requestId += 1;
        const requestId = task.requestId;
        const controller = new AbortController();
        activeControllers.set(task.id, controller);
        clearTaskResult(task);
        task.status = 'uploading';
        task.stage = 'STARTING';
        task.message = '任务启动中...';
        task.progress = 0;
        task.progressData = null;
        renderTaskList();
        updateOverallProgress();
        if (task.id === selectedTaskId) {
            updateResultPanel();
            setActionButtonsDisabledState();
        }

        const formData = new FormData();
        formData.append('audio_file', task.file);

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
                if (!isCurrentTaskRequest(task, requestId)) {
                    break;
                }
                buffer += decoder.decode(value, { stream: true });
                buffer = parseSSEChunk(buffer, function(eventData) {
                    if (isCurrentTaskRequest(task, requestId)) {
                        handleTaskProgressEvent(task, eventData);
                    }
                });
            }
        } catch (error) {
            if (!isCurrentTaskRequest(task, requestId)) {
                return;
            }
            if (error.name === 'AbortError') {
                task.status = 'cancelled';
                task.message = '任务已取消';
                task.errorMessage = '任务已取消';
            } else {
                console.error('转录错误:', error);
                task.status = 'error';
                task.message = error.message;
                task.errorMessage = error.message;
            }
            renderTaskList();
            updateOverallProgress();
            if (task.id === selectedTaskId) {
                updateResultPanel();
            }
        } finally {
            if (activeControllers.get(task.id) === controller) {
                activeControllers.delete(task.id);
            }
            updateOverallProgress();
            if (task.id === selectedTaskId) {
                setActionButtonsDisabledState();
            }
        }
    }

    async function startBatchTranscription() {
        const runnableTasks = tasks.filter((task) => ['pending', 'error', 'cancelled'].includes(task.status));
        if (!runnableTasks.length) {
            return;
        }

        isBatchRunning = true;
        setActionButtonsDisabledState();
        updateOverallProgress();

        try {
            await Promise.allSettled(runnableTasks.map((task) => submitTaskTranscription(task)));
        } finally {
            isBatchRunning = false;
            updateOverallProgress();
            renderTaskList();
            setActionButtonsDisabledState();

            const successCount = tasks.filter((task) => task.status === 'success').length;
            const errorCount = tasks.filter((task) => task.status === 'error').length;
            if (successCount && !errorCount) {
                updateStatus(`全部 ${successCount} 个文件已处理完成。`, 'success');
            } else if (successCount || errorCount) {
                updateStatus(`批量处理结束：成功 ${successCount} 个，失败 ${errorCount} 个。`, errorCount ? 'info' : 'success');
            }
        }
    }

    function abortAllActiveRequests(silent) {
        activeControllers.forEach((controller) => controller.abort());
        activeControllers.clear();
        isBatchRunning = false;
        if (!silent) {
            updateStatus('当前批量转录任务已取消。', 'info');
        }
    }

    function appendTasks(files) {
        const selectedFiles = getAudioFilesFromSelection(files);
        if (!selectedFiles.length) {
            updateFileNameDisplay(tasks.map((task) => task.file));
            if (files && files.length) {
                updateStatus('没有发现可导入的音频文件。', 'info');
            }
            return;
        }

        const knownFiles = new Set(tasks.map((task) => fileIdentity(task.file)));
        const newTasks = [];
        selectedFiles.forEach((file) => {
            const identity = fileIdentity(file);
            if (knownFiles.has(identity)) {
                return;
            }
            knownFiles.add(identity);
            newTasks.push(createTask(file));
        });

        if (!newTasks.length) {
            updateStatus('所选文件已在任务列表中。', 'info');
            audioFileInput.value = '';
            return;
        }

        tasks = tasks.concat(newTasks);
        if (!selectedTaskId) {
            selectedTaskId = newTasks[0].id;
        }
        updateFileNameDisplay(tasks.map((task) => task.file));
        renderTaskList();
        updateOverallProgress();
        updateResultPanel();
        setActionButtonsDisabledState();
        audioFileInput.value = '';

        if (hasActiveTasks()) {
            startBatchTranscription();
        }
    }

    audioFileInput.addEventListener('change', function(event) {
        appendTasks(event.target.files);
    });

    audioFolderInput.addEventListener('change', function(event) {
        appendTasks(event.target.files);
        audioFolderInput.value = '';
    });

    ['dragenter', 'dragover'].forEach((eventName) => {
        uploadDropzone.addEventListener(eventName, function(event) {
            event.preventDefault();
            event.stopPropagation();
            uploadDropzone.classList.add('drag-active');
        });
    });

    ['dragleave', 'dragend'].forEach((eventName) => {
        uploadDropzone.addEventListener(eventName, function(event) {
            event.preventDefault();
            event.stopPropagation();
            if (event.relatedTarget && uploadDropzone.contains(event.relatedTarget)) {
                return;
            }
            uploadDropzone.classList.remove('drag-active');
        });
    });

    uploadDropzone.addEventListener('drop', function(event) {
        event.preventDefault();
        event.stopPropagation();
        uploadDropzone.classList.remove('drag-active');
        if (event.dataTransfer && event.dataTransfer.files) {
            appendTasks(event.dataTransfer.files);
        }
    });

    submitBtn.addEventListener('click', function(event) {
        event.preventDefault();
        if (!tasks.length) {
            updateStatus('请先选择至少一个音频文件。', 'error');
            return;
        }
        startBatchTranscription();
    });

    recalibrateBtn.addEventListener('click', async function() {
        const task = getSelectedTask();
        if (!task || !task.rawTranscription) {
            updateStatus('没有可供重新校准的原始转录文本。', 'info');
            return;
        }

        updateStatus(`正在重新校准 ${task.file.name}...`, 'info');
        setActionButtonsDisabledState();
        const originalText = recalibrateBtn.textContent;
        recalibrateBtn.textContent = '校准中...';

        try {
            const response = await fetch('/api/recalibrate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ raw_transcription: task.rawTranscription }),
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || `请求失败 (状态 ${response.status})`);
            }
            task.transcription = data.transcription;
            task.calibrationMessage = data.calibration_message;
            task.isCalibrated = Boolean(data.is_calibrated);
            task.status = 'success';
            task.message = data.calibration_message || '重新校准完成';
            task.errorMessage = null;
            resetGeneratedViews(task);
            renderTaskList();
            updateResultPanel();
        } catch (error) {
            console.error('重新校准错误:', error);
            updateStatus(`重新校准时发生错误: ${error.message}`, 'error');
        } finally {
            recalibrateBtn.textContent = originalText;
            setActionButtonsDisabledState();
        }
    });

    summarizeBtn.addEventListener('click', async function() {
        const task = getSelectedTask();
        if (!task || !task.transcription || task.transcription.trim() === '') {
            updateStatus('没有可供总结的文本。', 'info');
            return;
        }

        if (task.summaryText) {
            task.isShowingSummary = !task.isShowingSummary;
            task.isShowingNotes = false;
            summarizeBtn.textContent = task.isShowingSummary ? '显示原文' : '显示摘要';
            generateNotesBtn.textContent = task.notesText && task.isShowingNotes ? '显示原文' : '生成笔记';
            updateResultPanel();
            setActionButtonsDisabledState();
            return;
        }

        updateStatus(`正在生成 ${task.file.name} 的摘要...`, 'info');
        setActionButtonsDisabledState();
        const originalText = summarizeBtn.textContent;
        summarizeBtn.textContent = '生成中...';

        try {
            const response = await fetch('/api/summarize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text_to_summarize: task.transcription }),
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || `请求失败 (状态 ${response.status})`);
            }
            task.summaryText = data.summary;
            task.isShowingSummary = true;
            task.isShowingNotes = false;
            summarizeBtn.textContent = '显示原文';
            updateResultPanel();
            updateStatus('摘要生成成功！', 'success');
        } catch (error) {
            console.error('生成摘要错误:', error);
            updateStatus(`生成摘要失败 (${error.message})，请重试...`, 'error');
            summarizeBtn.textContent = originalText;
        } finally {
            setActionButtonsDisabledState();
        }
    });

    generateNotesBtn.addEventListener('click', async function() {
        const task = getSelectedTask();
        if (!task || !task.transcription || task.transcription.trim() === '') {
            updateStatus('没有可供生成笔记的文本。', 'info');
            return;
        }

        if (task.notesText) {
            task.isShowingNotes = !task.isShowingNotes;
            task.isShowingSummary = false;
            generateNotesBtn.textContent = task.isShowingNotes ? '显示原文' : '显示笔记';
            summarizeBtn.textContent = task.summaryText && task.isShowingSummary ? '显示原文' : '量子速读';
            updateResultPanel();
            setActionButtonsDisabledState();
            return;
        }

        updateStatus(`正在生成 ${task.file.name} 的笔记...`, 'info');
        setActionButtonsDisabledState();
        const originalText = generateNotesBtn.textContent;
        generateNotesBtn.textContent = '生成中...';

        try {
            const response = await fetch('/api/generatenote', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ text_to_process: task.transcription }),
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || `请求失败 (状态 ${response.status})`);
            }
            task.notesText = data.notes;
            task.isShowingNotes = true;
            task.isShowingSummary = false;
            generateNotesBtn.textContent = '显示原文';
            updateResultPanel();
            updateStatus('笔记生成成功！', 'success');
        } catch (error) {
            console.error('生成笔记错误:', error);
            updateStatus(`生成笔记失败 (${error.message})，请重试...`, 'error');
            generateNotesBtn.textContent = originalText;
        } finally {
            setActionButtonsDisabledState();
        }
    });

    copyBtn.addEventListener('click', function() {
        const task = getSelectedTask();
        const textToCopy = getVisibleTaskText(task);
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

    exportMarkdownBtn.addEventListener('click', function() {
        exportMarkdown();
    });

    window.addEventListener('beforeunload', function() {
        abortAllActiveRequests(true);
    });

    window.addEventListener('pagehide', function() {
        abortAllActiveRequests(true);
    });

    renderTaskList();
    updateOverallProgress();
    updateResultPanel();
    setActionButtonsDisabledState();
});
