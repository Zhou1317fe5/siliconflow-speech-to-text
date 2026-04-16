from __future__ import annotations

import json
import queue
import threading
import time
from typing import Optional

from flask import Response, jsonify, render_template, request, stream_with_context
from werkzeug.exceptions import RequestEntityTooLarge

from .errors import (
    AppError,
    AuthenticationError,
    ConfigurationError,
    OperationCancelled,
    UploadTooLargeError,
    ValidationError,
)
from .uploads import prepare_uploaded_audio
from .workflows import (
    ServiceContainer,
    build_calibration_status_message,
    perform_notes_generation,
    perform_summarization,
    perform_text_optimization,
    transcribe_audio,
)


class HeartbeatThread(threading.Thread):
    def __init__(self, heartbeat_queue: queue.Queue[str], interval: int = 15):
        super().__init__(daemon=True)
        self.heartbeat_queue = heartbeat_queue
        self.interval = interval
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.wait(self.interval):
            try:
                self.heartbeat_queue.put(": heartbeat\n\n", block=False)
            except queue.Full:
                pass

    def stop(self) -> None:
        self.stop_event.set()


def send_progress_event(stage: str, message: str, progress: int, data: Optional[dict] = None) -> str:
    event_data = {"stage": stage, "message": message, "progress": progress}
    if data is not None:
        event_data["data"] = data
    return f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"


def _json_error_response(error: AppError) -> tuple[Response, int]:
    return jsonify({"error": error.message}), error.status_code


def _openai_error_response(error: AppError) -> tuple[Response, int]:
    payload = {
        "error": {
            "message": error.message,
            "type": error.error_type,
        }
    }
    if error.code:
        payload["error"]["code"] = error.code
    return jsonify(payload), error.status_code


def _sse_error_response(message: str, status_code: int) -> Response:
    def generator():
        yield send_progress_event("ERROR", message, 100)

    return Response(
        stream_with_context(generator()),
        status=status_code,
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


def _require_text_field(field_name: str, label: str) -> str:
    data = request.get_json(silent=True)
    if not data or field_name not in data:
        raise ValidationError(f"请求体无效或缺少 '{field_name}' 字段")
    value = data.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label}不能为空")
    return value


def register_routes(app, services: ServiceContainer) -> None:
    @app.before_request
    def check_openai_auth():
        if not request.path.startswith("/v1/"):
            return None
        if not services.config.api_access_token:
            return _openai_error_response(
                ConfigurationError("API access is not configured on the server.")
            )

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return _openai_error_response(
                AuthenticationError("Authorization header is missing or invalid.")
            )

        token = auth_header.split(" ", 1)[1]
        if token != services.config.api_access_token:
            return _openai_error_response(AuthenticationError("Incorrect API key provided."))
        return None

    @app.errorhandler(RequestEntityTooLarge)
    def handle_request_entity_too_large(_error):
        message = f"上传文件超过限制，最大允许 {services.config.max_upload_size_mb} MB"
        if request.path == "/api/transcribe-stream":
            return _sse_error_response(message, 413)
        if request.path.startswith("/v1/"):
            return _openai_error_response(UploadTooLargeError(message))
        return _json_error_response(UploadTooLargeError(message))

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/v1/models", methods=["GET"])
    def list_models():
        created = int(time.time())
        return jsonify(
            {
                "object": "list",
                "data": [
                    {
                        "id": services.config.model_calibrate,
                        "object": "model",
                        "created": created,
                        "owned_by": "xy2yp",
                    },
                    {
                        "id": services.config.model_summarize,
                        "object": "model",
                        "created": created,
                        "owned_by": "xy2yp",
                    },
                ],
            }
        )

    @app.route("/v1/audio/transcriptions", methods=["POST"])
    def openai_audio_transcriptions():
        if "file" not in request.files:
            return _openai_error_response(ValidationError("No file part in the request"))

        model_requested = request.form.get("model")
        if model_requested not in [
            services.config.model_calibrate,
            services.config.model_summarize,
        ]:
            return _openai_error_response(
                ValidationError(
                    f"Model '{model_requested}' is not supported. Please use "
                    f"'{services.config.model_calibrate}' or '{services.config.model_summarize}'."
                )
            )

        try:
            uploaded_audio = prepare_uploaded_audio(request.files["file"], services.config)
            raw_transcription = transcribe_audio(uploaded_audio, services)
            calibrated_text, opt_message, is_calibrated = perform_text_optimization(
                raw_transcription, services
            )

            final_response: dict[str, object] = {}
            if model_requested == services.config.model_calibrate:
                final_response["text"] = calibrated_text
                if not is_calibrated:
                    final_response["x_warning"] = {
                        "code": "calibration_failed",
                        "message": (
                            "Text optimization failed. Returning raw transcription. "
                            f"Reason: {opt_message}"
                        ),
                    }
            else:
                if not is_calibrated:
                    final_response["text"] = raw_transcription
                    final_response["x_warning"] = {
                        "code": "calibration_failed_in_summary_workflow",
                        "message": (
                            "The calibration step failed. Returning the raw, un-calibrated "
                            f"transcription as a fallback. Reason: {opt_message}"
                        ),
                    }
                else:
                    try:
                        final_response["text"] = perform_summarization(calibrated_text, services)
                    except AppError as summary_error:
                        final_response["text"] = calibrated_text
                        final_response["x_warning"] = {
                            "code": "summarization_failed",
                            "message": (
                                "Final summarization step failed. Returning the full calibrated "
                                f"text instead. Reason: {summary_error.message}"
                            ),
                        }

            return jsonify(final_response)
        except AppError as error:
            return _openai_error_response(error)
        finally:
            uploaded_audio = locals().get("uploaded_audio")
            if uploaded_audio:
                uploaded_audio.close()

    @app.route("/api/transcribe", methods=["POST"])
    def transcribe_and_optimize_audio():
        audio_file = request.files.get("audio_file")
        if not audio_file:
            return _json_error_response(ValidationError("缺少上传的音频文件"))

        try:
            uploaded_audio = prepare_uploaded_audio(audio_file, services.config)
            raw_transcription = transcribe_audio(uploaded_audio, services)
            final_transcription, opt_message, is_calibrated = perform_text_optimization(
                raw_transcription, services
            )
            final_status_message = build_calibration_status_message(opt_message, is_calibrated)
            return jsonify(
                {
                    "status": "success",
                    "transcription": final_transcription,
                    "raw_transcription": raw_transcription,
                    "calibration_message": final_status_message,
                    "is_calibrated": is_calibrated,
                }
            )
        except AppError as error:
            return _json_error_response(error)
        finally:
            uploaded_audio = locals().get("uploaded_audio")
            if uploaded_audio:
                uploaded_audio.close()

    @app.route("/api/transcribe-stream", methods=["POST"])
    def transcribe_and_optimize_audio_stream():
        audio_file = request.files.get("audio_file")
        if not audio_file:
            return _sse_error_response("缺少上传的音频文件", 400)

        try:
            uploaded_audio = prepare_uploaded_audio(audio_file, services.config)
        except AppError as error:
            return _sse_error_response(error.message, error.status_code)

        cancel_event = threading.Event()
        progress_queue: queue.Queue[str] = queue.Queue(maxsize=100)
        heartbeat_queue: queue.Queue[str] = queue.Queue(maxsize=10)
        heartbeat_thread: Optional[HeartbeatThread] = None
        result_container: dict[str, object] = {}
        error_container: dict[str, Exception] = {}
        progress_state: dict[str, object] = {}
        optimization_started_at: Optional[float] = None

        def progress_callback(
            stage: str,
            message: str,
            progress: int,
            data: Optional[dict[str, int]] = None,
        ) -> None:
            progress_state["stage"] = stage
            progress_state["message"] = message
            progress_state["progress"] = progress
            progress_state["data"] = data or {}
            try:
                progress_queue.put_nowait(send_progress_event(stage, message, progress, data))
            except queue.Full:
                pass

        def optimization_worker(raw_transcription: str) -> None:
            try:
                result_container["result"] = perform_text_optimization(
                    raw_transcription,
                    services,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                )
            except Exception as exc:
                error_container["error"] = exc

        def generate_progress():
            nonlocal heartbeat_thread
            worker: Optional[threading.Thread] = None
            try:
                yield send_progress_event("STARTING", "任务启动中...", 5)
                yield send_progress_event("S2T_START", "正在进行语音识别...", 10)
                raw_transcription = transcribe_audio(uploaded_audio, services, cancel_event)
                yield send_progress_event("S2T_COMPLETE", "语音识别完成，获取到原始文本。", 40)
                yield send_progress_event("OPTIMIZATION_START", "开始文本校准...", 45)

                if len(raw_transcription) > services.config.chunk_processing_threshold:
                    optimization_started_at = time.time()
                    heartbeat_thread = HeartbeatThread(heartbeat_queue, interval=15)
                    heartbeat_thread.start()

                worker = threading.Thread(
                    target=optimization_worker,
                    args=(raw_transcription,),
                    daemon=True,
                )
                worker.start()

                while worker.is_alive() or not progress_queue.empty() or not heartbeat_queue.empty():
                    try:
                        yield progress_queue.get(timeout=0.5)
                        continue
                    except queue.Empty:
                        pass

                    try:
                        heartbeat_msg = heartbeat_queue.get_nowait()
                        yield heartbeat_msg
                        if progress_state.get("stage") == "OPTIMIZING_CHUNK":
                            elapsed = (
                                int(time.time() - optimization_started_at)
                                if optimization_started_at is not None
                                else None
                            )
                            message = str(progress_state.get("message") or "正在校准文本块...")
                            if elapsed is not None:
                                message = f"{message} 已耗时 {elapsed} 秒"
                            yield send_progress_event(
                                "OPTIMIZING_CHUNK",
                                message,
                                int(progress_state.get("progress") or 70),
                                progress_state.get("data") if isinstance(progress_state.get("data"), dict) else None,
                            )
                    except queue.Empty:
                        pass

                worker.join()

                if "error" in error_container:
                    raise error_container["error"]
                if "result" not in result_container:
                    raise RuntimeError("文本优化未返回结果")

                final_transcription, opt_message, is_calibrated = result_container["result"]
                yield send_progress_event("OPTIMIZATION_COMPLETE", "文本校准完成。", 95)
                yield send_progress_event(
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
            except GeneratorExit:
                cancel_event.set()
                raise
            except OperationCancelled:
                cancel_event.set()
            except AppError as error:
                yield send_progress_event("ERROR", error.message, 100)
            except Exception as error:
                yield send_progress_event("ERROR", f"处理请求时发生未知错误: {type(error).__name__}", 100)
            finally:
                cancel_event.set()
                if heartbeat_thread:
                    heartbeat_thread.stop()
                    heartbeat_thread.join(timeout=2)
                if worker and worker.is_alive():
                    worker.join(timeout=0.2)
                uploaded_audio.close()

        return Response(
            stream_with_context(generate_progress()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @app.route("/api/recalibrate", methods=["POST"])
    def recalibrate_text():
        try:
            raw_text = _require_text_field("raw_transcription", "需要重新校准的文本")
            calibrated_text, calibration_status_msg, calibration_success = perform_text_optimization(
                raw_text,
                services,
            )
            return jsonify(
                {
                    "status": "success",
                    "transcription": calibrated_text,
                    "calibration_message": calibration_status_msg,
                    "is_calibrated": calibration_success,
                }
            )
        except AppError as error:
            return _json_error_response(error)

    @app.route("/api/summarize", methods=["POST"])
    def summarize_text():
        try:
            text = _require_text_field("text_to_summarize", "待总结的文本")
            return jsonify({"summary": perform_summarization(text, services)})
        except AppError as error:
            return _json_error_response(error)

    @app.route("/api/generatenote", methods=["POST"])
    def generate_notes():
        try:
            text = _require_text_field("text_to_process", "待处理的文本")
            return jsonify({"status": "success", "notes": perform_notes_generation(text, services)})
        except AppError as error:
            return _json_error_response(error)
