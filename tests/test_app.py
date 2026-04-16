from __future__ import annotations

import io

import pytest

from speech_to_text import create_app
from speech_to_text.clients import ChatCompletionClient
from speech_to_text.config import AppConfig
from speech_to_text.errors import ConfigurationError
from speech_to_text.prompts import PROMPT_GENERATE_NOTES
from speech_to_text.workflows import (
    ServiceContainer,
    perform_notes_generation,
    split_text_intelligently,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


@pytest.fixture
def app():
    return create_app(
        {
            "S2T_API_KEY": "test-s2t-key",
            "OPT_API_KEY": "test-opt-key",
            "OPT_MODEL": "test-model",
            "API_ACCESS_TOKEN": "secret-token",
            "MAX_UPLOAD_SIZE_MB": "1",
        }
    )


@pytest.fixture
def client(app):
    return app.test_client()


def test_transcribe_route_calls_s2t_once_and_returns_success(client, monkeypatch):
    call_count = {"s2t": 0}

    def fake_transcribe(uploaded_audio, services, cancel_event=None):
        call_count["s2t"] += 1
        return "原始文本"

    def fake_optimize(raw_text, services, progress_callback=None, cancel_event=None):
        assert raw_text == "原始文本"
        return "校准后文本", "校准成功！", True

    monkeypatch.setattr("speech_to_text.routes.transcribe_audio", fake_transcribe)
    monkeypatch.setattr("speech_to_text.routes.perform_text_optimization", fake_optimize)

    response = client.post(
        "/api/transcribe",
        data={"audio_file": (io.BytesIO(b"audio-bytes"), "test.wav")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "success"
    assert payload["transcription"] == "校准后文本"
    assert payload["raw_transcription"] == "原始文本"
    assert call_count["s2t"] == 1


def test_transcribe_stream_emits_expected_events(client, monkeypatch):
    def fake_transcribe(uploaded_audio, services, cancel_event=None):
        return "这是一段足够长的原始文本" * 400

    def fake_optimize(raw_text, services, progress_callback=None, cancel_event=None):
        assert progress_callback is not None
        progress_callback("OPTIMIZING_CHUNK", "正在校准文本块 (1/2)...", 60)
        progress_callback("OPTIMIZING_CHUNK", "正在校准文本块 (2/2)...", 85)
        return "校准后长文本", "校准成功！", True

    monkeypatch.setattr("speech_to_text.routes.transcribe_audio", fake_transcribe)
    monkeypatch.setattr("speech_to_text.routes.perform_text_optimization", fake_optimize)

    response = client.post(
        "/api/transcribe-stream",
        data={"audio_file": (io.BytesIO(b"stream-audio"), "test.wav")},
        content_type="multipart/form-data",
    )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '"stage": "STARTING"' in body
    assert '"stage": "S2T_START"' in body
    assert '"stage": "S2T_COMPLETE"' in body
    assert '"stage": "OPTIMIZATION_START"' in body
    assert '"stage": "OPTIMIZING_CHUNK"' in body
    assert '"stage": "OPTIMIZATION_COMPLETE"' in body
    assert '"stage": "DONE"' in body


def test_upload_limit_returns_413_for_large_payload():
    app = create_app(
        {
            "S2T_API_KEY": "test-s2t-key",
            "OPT_API_KEY": "test-opt-key",
            "OPT_MODEL": "test-model",
            "MAX_UPLOAD_SIZE_MB": "1",
        }
    )
    client = app.test_client()
    response = client.post(
        "/api/transcribe",
        data={"audio_file": (io.BytesIO(b"x" * (1024 * 1024 + 1)), "big.wav")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 413
    assert "超过限制" in response.get_json()["error"]


def test_openai_route_requires_auth(client):
    response = client.get("/v1/models")
    assert response.status_code == 401
    payload = response.get_json()
    assert payload["error"]["type"] == "invalid_request_error"


def test_api_summarize_rejects_non_string_input(client):
    response = client.post("/api/summarize", json={"text_to_summarize": 123})
    assert response.status_code == 400
    assert "不能为空" in response.get_json()["error"]


def test_chat_completion_retries_empty_content(monkeypatch):
    responses = [
        FakeResponse(200, {"choices": [{"message": {"content": ""}}]}),
        FakeResponse(200, {"choices": [{"message": {"content": "成功内容"}}]}),
    ]

    def fake_post(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr("speech_to_text.clients.requests.post", fake_post)

    client = ChatCompletionClient(
        AppConfig.from_mapping({"OPT_API_KEY": "k", "OPT_MODEL": "m", "CALIBRATION_MODEL": "m"})
    )
    content = client.complete(
        model="m",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.1,
        empty_message="API返回空内容",
        feature_name="校准",
    )
    assert content == "成功内容"
    assert responses == []


def test_long_notes_generation_uses_chunk_map_reduce():
    config = AppConfig.from_mapping(
        {
            "OPT_API_KEY": "k",
            "OPT_MODEL": "m",
            "NOTES_MODEL": "m",
            "CHUNK_TARGET_SIZE": "20",
            "CHUNK_PROCESSING_THRESHOLD": "25",
        }
    )
    source_text = "这是一段很长的文本。" * 20
    expected_chunks = split_text_intelligently(source_text, config.chunk_target_size)

    class FakeChatClient:
        def __init__(self):
            self.system_prompts = []
            self.user_inputs = []

        def ensure_configured(self, model, feature_name):
            return None

        def complete(
            self,
            *,
            model,
            messages,
            temperature,
            empty_message,
            feature_name,
            cancel_event=None,
        ):
            self.system_prompts.append(messages[0]["content"])
            self.user_inputs.append(messages[1]["content"])
            if len(self.user_inputs) <= len(expected_chunks):
                return "# 学术笔记\n\n## 片段要点\n- 信息"
            return "# 汇总笔记\n\n## 1. 主题\n- 综合内容"

    fake_chat = FakeChatClient()
    services = ServiceContainer(config=config, s2t_client=None, chat_client=fake_chat)

    notes = perform_notes_generation(source_text, services)
    assert notes.startswith("# ")
    assert len(fake_chat.user_inputs) == len(expected_chunks) + 1
    assert all(prompt == PROMPT_GENERATE_NOTES for prompt in fake_chat.system_prompts)
    assert fake_chat.user_inputs[-1].count("# 学术笔记") == 0
    assert fake_chat.user_inputs[-1].count("## 片段要点") == len(expected_chunks)


def test_validate_runtime_rejects_invalid_s2t_url():
    config = AppConfig.from_mapping(
        {
            "S2T_API_URL": "not-a-url",
            "S2T_API_KEY": "k",
        }
    )
    with pytest.raises(ConfigurationError):
        config.validate_runtime()
