from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import requests

from .config import AppConfig
from .errors import (
    ConfigurationError,
    OperationCancelled,
    UpstreamServiceError,
    UpstreamTimeoutError,
)
from .uploads import UploadedAudio

CLIENT_ERROR_STATUSES = {400, 401, 403, 404, 409, 422, 429}


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
class SpeechRecognitionClient:
    config: AppConfig

    def transcribe(self, uploaded_audio: UploadedAudio, cancel_event: Optional[object] = None) -> str:
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
