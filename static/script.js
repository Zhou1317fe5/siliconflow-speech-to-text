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

    const TASK_STORAGE_KEY = 'speech_to_text_tasks_v2';
    const POLL_INTERVAL_MS = 2000;
    const ACTIVE_STATUSES = new Set(['uploading', 'queued', 'processing']);
    const RETRYABLE_STATUSES = new Set(['pending', 'error', 'cancelled']);
    const AUDIO_EXTENSIONS = new Set([
        '.aac', '.aiff', '.amr', '.flac', '.m4a', '.m4b', '.mid', '.midi', '.mp3',
        '.oga', '.ogg', '.opus', '.ra', '.wav', '.weba', '.webm', '.wma',
    ]);

    let tasks = [];
    let selectedTaskId = null;
    let isBatchRunning = false;
    let nextTaskId = 1;
    let pollTimer = null;
    let pollInFlight = false;
    const activeControllers = new Map();

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

    function createTask(file, persisted) {
        const data = persisted || {};
        const taskId = Number.isInteger(data.id) ? data.id : nextTaskId++;
        nextTaskId = Math.max(nextTaskId, taskId + 1);
        return {
            id: taskId,
            serverTaskId: typeof data.serverTaskId === 'string' ? data.serverTaskId : null,
            file: file || null,
            filename: data.filename || (file ? file.name : '未命名文件'),
            status: data.status || 'pending',
            stage: data.stage || 'PENDING',
            message: data.message || '等待开始',
            progress: Number.isFinite(data.progress) ? data.progress : 0,
            progressData: data.progressData || null,
            rawTranscription: data.rawTranscription || null,
            transcription: data.transcription || null,
            calibrationMessage: data.calibrationMessage || null,
            isCalibrated: Boolean(data.isCalibrated),
            summaryText: data.summaryText || null,
            isShowingSummary: Boolean(data.isShowingSummary),
            notesText: data.notesText || null,
            isShowingNotes: Boolean(data.isShowingNotes),
            errorMessage: data.errorMessage || null,
            requestId: 0,
        };
    }

    function serializeTask(task) {
        return {
            id: task.id,
            serverTaskId: task.serverTaskId,
            filename: task.filename,
            status: task.status,
            stage: task.stage,
            message: task.message,
            progress: task.progress,
            progressData: task.progressData,
            rawTranscription: task.rawTranscription,
            transcription: task.transcription,
            calibrationMessage: task.calibrationMessage,
            isCalibrated: task.isCalibrated,
            summaryText: task.summaryText,
            isShowingSummary: task.isShowingSummary,
            notesText: task.notesText,
            isShowingNotes: task.isShowingNotes,
            errorMessage: task.errorMessage,
        };
    }

    function persistTasks() {
        const payload = {
            selectedTaskId,
            nextTaskId,
            tasks: tasks.map(serializeTask),
        };
        try {
            localStorage.setItem(TASK_STORAGE_KEY, JSON.stringify(payload));
        } catch (error) {
            console.error('无法保存任务状态:', error);
        }
    }

    function restoreTasks() {
        try {
            const raw = localStorage.getItem(TASK_STORAGE_KEY);
            if (!raw) {
                return;
            }
            const parsed = JSON.parse(raw);
            if (!parsed || !Array.isArray(parsed.tasks)) {
                return;
            }
            tasks = parsed.tasks.map((task) => createTask(null, task));
            if (Number.isInteger(parsed.nextTaskId)) {
                nextTaskId = Math.max(nextTaskId, parsed.nextTaskId);
            }
            if (Number.isInteger(parsed.selectedTaskId)) {
                selectedTaskId = parsed.selectedTaskId;
            }
            if (!getTaskById(selectedTaskId) && tasks.length) {
                selectedTaskId = tasks[0].id;
            }
        } catch (error) {
            console.error('恢复任务状态失败:', error);
            tasks = [];
            selectedTaskId = null;
        }
    }

    function getTaskById(taskId) {
        return tasks.find((task) => task.id === taskId) || null;
    }

    function getSelectedTask() {
        return getTaskById(selectedTaskId);
    }

    function getTaskName(task) {
        return task ? task.filename : '';
    }

    function hasTaskSourceFile(task) {
        return Boolean(task && task.file);
    }

    function hasActiveTasks() {
        return tasks.some((task) => ACTIVE_STATUSES.has(task.status));
    }

    function hasRunnableTasks() {
        return tasks.some((task) => hasTaskSourceFile(task) && RETRYABLE_STATUSES.has(task.status));
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
            lines.push(`## ${index + 1}. ${getTaskName(task)}`);
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
        const firstTaskName = tasks.length ? sanitizeFilenamePart(getTaskName(tasks[0]).replace(/\.[^.]+$/, '')) : 'batch';
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
        const activeCount = tasks.filter((task) => ACTIVE_STATUSES.has(task.status)).length;
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
        resultTitle.textContent = task ? `转录结果 · ${getTaskName(task)}` : '转录结果';
        transcriptionResult.textContent = getVisibleTaskText(task);
        syncActionButtonLabels();

        if (!task) {
            updateStatus(null, null);
            return;
        }

        if (task.status === 'error') {
            updateStatus(`文件 ${getTaskName(task)} 处理失败: ${task.errorMessage || task.message}`, 'error');
            return;
        }
        if (task.status === 'cancelled') {
            updateStatus(`文件 ${getTaskName(task)} 的任务已取消。`, 'info');
            return;
        }
        if (task.status === 'success') {
            updateStatus(
                task.calibrationMessage || `文件 ${getTaskName(task)} 处理完成。`,
                task.isCalibrated ? 'success' : 'info'
            );
            return;
        }
        if (task.status !== 'pending') {
            updateStatus(`文件 ${getTaskName(task)}: ${task.message}`, 'info');
            return;
        }
        updateStatus(`已选择文件 ${getTaskName(task)}，等待开始处理。`, 'info');
    }

    function setActionButtonsDisabledState() {
        const task = getSelectedTask();
        const hasContent = Boolean(task && task.transcription && task.transcription.trim() !== '');
        const selectedTaskBusy = Boolean(task && ACTIVE_STATUSES.has(task.status));
        const visibleText = getVisibleTaskText(task);

        submitBtn.disabled = !hasRunnableTasks();
        recalibrateBtn.disabled = !hasContent || selectedTaskBusy;
        summarizeBtn.disabled = !hasContent || selectedTaskBusy;
        generateNotesBtn.disabled = !hasContent || selectedTaskBusy;
        copyBtn.disabled = !visibleText.trim();
        exportMarkdownBtn.disabled = !canExportMarkdown();
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
        const activeCount = tasks.filter((task) => ACTIVE_STATUSES.has(task.status)).length;
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
            name.textContent = getTaskName(task);
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

    function setSelectedTask(taskId) {
        selectedTaskId = taskId;
        renderTaskList();
        updateResultPanel();
        setActionButtonsDisabledState();
        persistTasks();
    }

    function renderAll() {
        renderTaskList();
        updateOverallProgress();
        updateResultPanel();
        setActionButtonsDisabledState();
        persistTasks();
    }

    function updateFileNameDisplay(entries) {
        const items = (entries || []).filter(Boolean);
        if (!items.length) {
            fileNameDisplay.textContent = '未选择文件，可重复点击追加';
            return;
        }
        const preview = items.slice(0, 3).map((entry) => typeof entry === 'string' ? entry : entry.name).join('、');
        if (items.length <= 3) {
            fileNameDisplay.textContent = `已加入 ${items.length} 个文件：${preview}`;
            return;
        }
        fileNameDisplay.textContent = `已加入 ${items.length} 个文件：${preview} 等`;
    }

    function clearTaskResult(task) {
        task.serverTaskId = null;
        task.rawTranscription = null;
        task.transcription = null;
        task.calibrationMessage = null;
        task.isCalibrated = false;
        task.errorMessage = null;
        resetGeneratedViews(task);
    }

    function applyServerTaskSnapshot(task, snapshot) {
        task.serverTaskId = snapshot.id || task.serverTaskId;
        task.filename = snapshot.filename || task.filename;
        task.status = snapshot.status || task.status;
        task.stage = snapshot.stage || task.stage;
        task.message = snapshot.message || task.message;
        task.progress = Number.isFinite(snapshot.progress) ? snapshot.progress : task.progress;
        task.progressData = snapshot.progress_data || null;
        task.rawTranscription = snapshot.raw_transcription || null;
        task.transcription = snapshot.transcription || null;
        task.calibrationMessage = snapshot.calibration_message || null;
        task.isCalibrated = Boolean(snapshot.is_calibrated);
        task.errorMessage = snapshot.error_message || null;
    }

    function getPollTaskIds() {
        return tasks
            .filter((task) => task.serverTaskId && ACTIVE_STATUSES.has(task.status))
            .map((task) => task.serverTaskId);
    }

    function stopPollingIfIdle() {
        if (getPollTaskIds().length || pollInFlight) {
            return;
        }
        if (pollTimer !== null) {
            window.clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    function ensurePolling() {
        if (getPollTaskIds().length && pollTimer === null) {
            pollTimer = window.setInterval(pollTaskSnapshots, POLL_INTERVAL_MS);
        }
        stopPollingIfIdle();
    }

    async function pollTaskSnapshots() {
        const ids = getPollTaskIds();
        if (!ids.length || pollInFlight) {
            stopPollingIfIdle();
            return;
        }

        pollInFlight = true;
        try {
            const response = await fetch(`/api/transcribe-tasks?ids=${encodeURIComponent(ids.join(','))}`);
            if (!response.ok) {
                throw new Error(`请求失败 (状态 ${response.status})`);
            }

            const payload = await response.json();
            const snapshots = Array.isArray(payload.tasks) ? payload.tasks : [];
            const snapshotMap = new Map(snapshots.map((task) => [task.id, task]));

            tasks.forEach((task) => {
                if (!task.serverTaskId || !ACTIVE_STATUSES.has(task.status)) {
                    return;
                }
                const snapshot = snapshotMap.get(task.serverTaskId);
                if (snapshot) {
                    applyServerTaskSnapshot(task, snapshot);
                    return;
                }
                task.status = 'error';
                task.message = '任务状态已丢失，可能是服务器已重启。';
                task.errorMessage = task.message;
            });

            renderAll();
        } catch (error) {
            console.error('轮询任务状态失败:', error);
        } finally {
            pollInFlight = false;
            stopPollingIfIdle();
        }
    }

    async function submitTaskTranscription(task) {
        if (!hasTaskSourceFile(task)) {
            return;
        }

        task.requestId += 1;
        const requestId = task.requestId;
        const controller = new AbortController();
        activeControllers.set(task.id, controller);
        clearTaskResult(task);
        task.status = 'uploading';
        task.stage = 'UPLOADING';
        task.message = '正在上传并创建后台任务...';
        task.progress = 1;
        task.progressData = null;
        renderAll();

        const formData = new FormData();
        formData.append('audio_file', task.file);

        try {
            const response = await fetch('/api/transcribe-tasks', {
                method: 'POST',
                body: formData,
                signal: controller.signal,
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || `请求失败 (状态 ${response.status})`);
            }
            if (task.requestId !== requestId) {
                return;
            }
            if (data.task) {
                applyServerTaskSnapshot(task, data.task);
                ensurePolling();
            }
            renderAll();
        } catch (error) {
            if (task.requestId !== requestId) {
                return;
            }
            console.error('创建后台任务失败:', error);
            task.status = 'error';
            task.message = error.message;
            task.errorMessage = error.message;
            renderAll();
        } finally {
            if (activeControllers.get(task.id) === controller) {
                activeControllers.delete(task.id);
            }
        }
    }

    async function startBatchTranscription() {
        const runnableTasks = tasks.filter((task) => hasTaskSourceFile(task) && RETRYABLE_STATUSES.has(task.status));
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
            renderAll();

            const submittedCount = tasks.filter((task) => task.serverTaskId).length;
            const uploadErrorCount = tasks.filter((task) => task.status === 'error' && !task.serverTaskId).length;
            if (submittedCount && !uploadErrorCount) {
                updateStatus(`已提交 ${submittedCount} 个任务，后台处理中，刷新后可继续查看状态。`, 'info');
            } else if (submittedCount || uploadErrorCount) {
                updateStatus(
                    `任务提交结束：已提交 ${submittedCount} 个，提交失败 ${uploadErrorCount} 个。`,
                    uploadErrorCount ? 'info' : 'success'
                );
            }
        }
    }

    function appendTasks(files) {
        const selectedFiles = getAudioFilesFromSelection(files);
        if (!selectedFiles.length) {
            updateFileNameDisplay(tasks.map((task) => ({ name: getTaskName(task) })));
            if (files && files.length) {
                updateStatus('没有发现可导入的音频文件。', 'info');
            }
            return;
        }

        const knownFiles = new Set(tasks.filter((task) => task.file).map((task) => fileIdentity(task.file)));
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
        updateFileNameDisplay(tasks.map((task) => ({ name: getTaskName(task) })));
        renderAll();
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

        updateStatus(`正在重新校准 ${getTaskName(task)}...`, 'info');
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
            renderAll();
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
            renderAll();
            return;
        }

        updateStatus(`正在生成 ${getTaskName(task)} 的摘要...`, 'info');
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
            renderAll();
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
            renderAll();
            return;
        }

        updateStatus(`正在生成 ${getTaskName(task)} 的笔记...`, 'info');
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
            renderAll();
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

    restoreTasks();
    if (tasks.length && !selectedTaskId) {
        selectedTaskId = tasks[0].id;
    }
    updateFileNameDisplay(tasks.map((task) => ({ name: getTaskName(task) })));
    renderAll();
    ensurePolling();
    if (getPollTaskIds().length) {
        pollTaskSnapshots();
    }
});
