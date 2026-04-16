from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field
from typing import Callable, Iterator, Optional

import requests

from .config import AppConfig
from .errors import (
    ConfigurationError,
    OperationCancelled,
    ServiceBusyError,
    UpstreamServiceError,
    UpstreamTimeoutError,
)
from .uploads import UploadedAudio

CLIENT_ERROR_STATUSES = {400, 401, 403, 404, 409, 422, 429}
StatusCallback = Callable[[str, str, int, Optional[dict[str, object]]], None]


def extract_api_error_message(response: requests.Response) -> str:
    try:
        error_detail = response.json()
        if isinstance(error_detail, dict):
            api_err_msg = (
                error_detail.get("error", {}).get("message")
                or error_detail.get("message")
                or error_detail.get("detail")
            )
            if api_err_msg:
                return str(api_err_msg)
        return response.text[:200]
    except ValueError:
        return response.text[:200]


def sleep_with_cancel(seconds: int, cancel_event: Optional[object]) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if cancel_event and cancel_event.is_set():
            raise OperationCancelled()
        time.sleep(0.2)


@dataclass
class UpstreamConcurrencyController:
    config: AppConfig
    _s2t_slots: threading.BoundedSemaphore = field(init=False, repr=False)
    _llm_slots: threading.BoundedSemaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._s2t_slots = threading.BoundedSemaphore(self.config.s2t_max_concurrent)
        self._llm_slots = threading.BoundedSemaphore(self.config.llm_max_concurrent)

    def _slot_definition(self, slot_kind: str) -> tuple[threading.BoundedSemaphore, str, str, int]:
        if slot_kind == "s2t":
            return self._s2t_slots, "语音识别", "WAITING_FOR_S2T_SLOT", 9
        if slot_kind == "llm":
            return self._llm_slots, "文本处理", "WAITING_FOR_LLM_SLOT", 47
        raise ValueError(f"未知的上游槽位类型: {slot_kind}")

    @contextmanager
    def reserve(
        self,
        slot_kind: str,
        *,
        cancel_event: Optional[object] = None,
        progress_callback: Optional[StatusCallback] = None,
    ) -> Iterator[None]:
        semaphore, slot_label, waiting_stage, waiting_progress = self._slot_definition(slot_kind)
        started_waiting_at = time.monotonic()
        next_notice_at = started_waiting_at
        queued_sent = False

        while True:
            if cancel_event and cancel_event.is_set():
                raise OperationCancelled()

            if semaphore.acquire(timeout=0.5):
                break

            waited_seconds = int(time.monotonic() - started_waiting_at)
            if waited_seconds >= self.config.queue_wait_timeout_seconds:
                raise ServiceBusyError(
                    f"当前请求较多，等待{slot_label}处理槽位超时，请稍后重试。",
                    code=f"{slot_kind}_queue_timeout",
                )

            if progress_callback and not queued_sent:
                progress_callback(
                    "QUEUED",
                    "当前请求较多，正在排队等待可用处理槽位...",
                    max(5, waiting_progress - 2),
                    {"slot_kind": slot_kind, "wait_seconds": waited_seconds},
                )
                queued_sent = True

            now = time.monotonic()
            if progress_callback and now >= next_notice_at:
                progress_callback(
                    waiting_stage,
                    f"正在等待可用{slot_label}槽位... 已等待 {waited_seconds} 秒",
                    waiting_progress,
                    {"slot_kind": slot_kind, "wait_seconds": waited_seconds},
                )
                next_notice_at = now + 5

        try:
            yield
        finally:
            semaphore.release()


@dataclass
class SpeechRecognitionClient:
    config: AppConfig
    controller: Optional[UpstreamConcurrencyController] = None

    def __post_init__(self) -> None:
        if self.controller is None:
            self.controller = UpstreamConcurrencyController(self.config)

    def transcribe(
        self,
        uploaded_audio: UploadedAudio,
        cancel_event: Optional[object] = None,
        progress_callback: Optional[StatusCallback] = None,
    ) -> str:
        if cancel_event and cancel_event.is_set():
            raise OperationCancelled()
        if not self.config.s2t_api_key:
            raise ConfigurationError("语音转录服务配置不完整: 缺少 API Key")
        if not self.config.is_s2t_url_valid():
            raise ConfigurationError("语音转录服务配置不完整: API URL无效")

        headers = {"Authorization": f"Bearer {self.config.s2t_api_key}"}
        files = {"file": uploaded_audio.to_requests_file()}
        payload = {"model": self.config.s2t_model}

        try:
            with self.controller.reserve(
                "s2t",
                cancel_event=cancel_event,
                progress_callback=progress_callback,
            ):
                response = requests.post(
                    self.config.s2t_api_url,
                    files=files,
                    data=payload,
                    headers=headers,
                    timeout=self.config.upstream_timeout_seconds,
                )
        except requests.exceptions.Timeout as exc:
            raise UpstreamTimeoutError("调用 S2T API 超时") from exc
        except requests.exceptions.RequestException as exc:
            raise UpstreamServiceError(f"调用 S2T API 失败: {type(exc).__name__}") from exc

        if response.status_code != 200:
            raise UpstreamServiceError(
                f"S2T API 返回错误: {response.status_code} - {extract_api_error_message(response)}",
                code="s2t_failed",
            )

        try:
            raw_transcription = response.json().get("text", "").strip()
        except ValueError as exc:
            raise UpstreamServiceError("S2T API 返回了无效 JSON", code="s2t_invalid_json") from exc

        if not raw_transcription:
            raise UpstreamServiceError("S2T 服务未能识别出任何文本。", code="s2t_empty_text")
        return raw_transcription


@dataclass
class ChatCompletionClient:
    config: AppConfig
    controller: Optional[UpstreamConcurrencyController] = None

    def __post_init__(self) -> None:
        if self.controller is None:
            self.controller = UpstreamConcurrencyController(self.config)

    def ensure_configured(self, model: Optional[str], feature_name: str) -> None:
        missing_parts = self.config.get_missing_opt_parts(model)
        if missing_parts:
            raise ConfigurationError(f"{feature_name}服务配置不完整: {', '.join(missing_parts)}")

    def complete(
        self,
        *,
        model: Optional[str],
        messages: list[dict[str, str]],
        temperature: float,
        empty_message: str,
        feature_name: str,
        cancel_event: Optional[object] = None,
        progress_callback: Optional[StatusCallback] = None,
    ) -> str:
        self.ensure_configured(model, feature_name)

        payload = {"model": model, "messages": messages, "temperature": temperature}
        headers = {
            "Authorization": f"Bearer {self.config.opt_api_key}",
            "Content-Type": "application/json",
        }

        last_error = empty_message
        for attempt in range(self.config.retry_attempts):
            if cancel_event and cancel_event.is_set():
                raise OperationCancelled()

            try:
                with self.controller.reserve(
                    "llm",
                    cancel_event=cancel_event,
                    progress_callback=progress_callback,
                ):
                    response = requests.post(
                        self.config.opt_api_url,
                        headers=headers,
                        json=payload,
                        timeout=self.config.upstream_timeout_seconds,
                    )
                if response.status_code == 200:
                    data = response.json()
                    content = (
                        data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    )
                    if content:
                        return content
                    last_error = empty_message
                else:
                    last_error = (
                        f"API错误 {response.status_code}: {extract_api_error_message(response)}"
                    )
                    if response.status_code in CLIENT_ERROR_STATUSES:
                        raise UpstreamServiceError(last_error, code="upstream_client_error")
            except requests.exceptions.Timeout:
                last_error = "请求超时"
            except requests.exceptions.RequestException as exc:
                last_error = f"网络连接错误: {type(exc).__name__}"
            except ValueError as exc:
                last_error = f"响应解析失败: {type(exc).__name__}"

            if attempt == self.config.retry_attempts - 1:
                if last_error == "请求超时":
                    raise UpstreamTimeoutError(f"{feature_name}请求超时")
                raise UpstreamServiceError(last_error, code="completion_failed")

            sleep_with_cancel(2 * (attempt + 1), cancel_event)

        raise UpstreamServiceError(last_error, code="completion_failed")
