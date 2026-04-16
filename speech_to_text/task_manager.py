from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .errors import AppError, OperationCancelled
from .uploads import UploadedAudio
from .workflows import (
    ServiceContainer,
    build_calibration_status_message,
    perform_text_optimization,
    transcribe_audio,
)


def run_transcription_pipeline(
    uploaded_audio: UploadedAudio,
    services: ServiceContainer,
    progress_callback,
    *,
    cancel_event: Optional[object] = None,
) -> None:
    try:
        progress_callback("S2T_START", "正在进行语音识别...", 10)
        raw_transcription = transcribe_audio(
            uploaded_audio,
            services,
            cancel_event,
            progress_callback,
        )
        progress_callback("S2T_COMPLETE", "语音识别完成，获取到原始文本。", 40)
        progress_callback(
            "OPTIMIZATION_START",
            "开始文本校准...",
            45,
            {
                "enable_heartbeat": len(raw_transcription)
                > services.config.chunk_processing_threshold
            },
        )
        final_transcription, opt_message, is_calibrated = perform_text_optimization(
            raw_transcription,
            services,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
        progress_callback("OPTIMIZATION_COMPLETE", "文本校准完成。", 95)
        progress_callback(
            "DONE",
            "处理完成！",
            100,
            {
                "transcription": final_transcription,
                "raw_transcription": raw_transcription,
                "calibration_message": build_calibration_status_message(
                    opt_message, is_calibrated
                ),
                "is_calibrated": is_calibrated,
            },
        )
    except OperationCancelled:
        progress_callback("CANCELLED", "任务已取消", 100)
    except AppError as error:
        progress_callback("ERROR", error.message, 100)
    except Exception as error:
        progress_callback("ERROR", f"处理请求时发生未知错误: {type(error).__name__}", 100)


@dataclass
class TranscriptionTask:
    id: str
    filename: str
    status: str = "pending"
    stage: str = "PENDING"
    message: str = "等待开始"
    progress: int = 0
    progress_data: Optional[dict[str, object]] = None
    raw_transcription: Optional[str] = None
    transcription: Optional[str] = None
    calibration_message: Optional[str] = None
    is_calibrated: bool = False
    error_message: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "filename": self.filename,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "progress": self.progress,
            "progress_data": self.progress_data,
            "raw_transcription": self.raw_transcription,
            "transcription": self.transcription,
            "calibration_message": self.calibration_message,
            "is_calibrated": self.is_calibrated,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class BackgroundTaskManager:
    def __init__(self, services: ServiceContainer, max_tasks: int = 200):
        self.services = services
        self.max_tasks = max_tasks
        self._tasks: dict[str, TranscriptionTask] = {}
        self._task_order: list[str] = []
        self._lock = threading.Lock()

    def create_transcription_task(self, uploaded_audio: UploadedAudio) -> dict[str, object]:
        task_id = uuid.uuid4().hex
        task = TranscriptionTask(
            id=task_id,
            filename=uploaded_audio.filename,
            status="processing",
            stage="STARTING",
            message="任务启动中...",
            progress=5,
        )
        with self._lock:
            self._tasks[task_id] = task
            self._task_order.append(task_id)
            self._trim_tasks_locked()

        worker = threading.Thread(
            target=self._run_task,
            args=(task_id, uploaded_audio),
            daemon=True,
        )
        worker.start()
        return task.to_dict()

    def get_task(self, task_id: str) -> Optional[dict[str, object]]:
        with self._lock:
            task = self._tasks.get(task_id)
            return task.to_dict() if task else None

    def list_tasks(self, ids: Optional[list[str]] = None) -> list[dict[str, object]]:
        with self._lock:
            if ids:
                return [self._tasks[task_id].to_dict() for task_id in ids if task_id in self._tasks]
            return [self._tasks[task_id].to_dict() for task_id in reversed(self._task_order)]

    def _trim_tasks_locked(self) -> None:
        while len(self._task_order) > self.max_tasks:
            oldest_id = self._task_order.pop(0)
            self._tasks.pop(oldest_id, None)

    def _run_task(self, task_id: str, uploaded_audio: UploadedAudio) -> None:
        def progress_callback(
            stage: str,
            message: str,
            progress: int,
            data: Optional[dict[str, object]] = None,
        ) -> None:
            with self._lock:
                task = self._tasks.get(task_id)
                if task is None:
                    return

                task.stage = stage
                task.message = message
                task.progress = progress
                task.progress_data = data or None
                task.updated_at = time.time()

                if stage in {"QUEUED", "WAITING_FOR_S2T_SLOT", "WAITING_FOR_LLM_SLOT"}:
                    task.status = "queued"
                elif stage == "DONE":
                    task.status = "success"
                    if data:
                        task.raw_transcription = str(data.get("raw_transcription") or "") or None
                        task.transcription = str(data.get("transcription") or "") or None
                        task.calibration_message = str(data.get("calibration_message") or "") or None
                        task.is_calibrated = bool(data.get("is_calibrated"))
                        task.error_message = None
                elif stage == "ERROR":
                    task.status = "error"
                    task.error_message = message
                elif stage == "CANCELLED":
                    task.status = "cancelled"
                    task.error_message = message
                else:
                    task.status = "processing"

        try:
            run_transcription_pipeline(
                uploaded_audio,
                self.services,
                progress_callback,
            )
        finally:
            uploaded_audio.close()
