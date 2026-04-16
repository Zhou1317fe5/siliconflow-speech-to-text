from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional

from .clients import ChatCompletionClient, SpeechRecognitionClient
from .config import AppConfig
from .errors import AppError, OperationCancelled, UpstreamServiceError, ValidationError
from .prompts import (
    HARDCODED_OPTIMIZATION_PROMPT,
    PROMPT_GENERATE_NOTES,
    PROMPT_SUMMARY_MAP,
    PROMPT_SUMMARY_REDUCE,
)
from .uploads import UploadedAudio


ProgressCallback = Callable[[str, str, int], None]


@dataclass
class ServiceContainer:
    config: AppConfig
    s2t_client: SpeechRecognitionClient
    chat_client: ChatCompletionClient


def split_text_intelligently(text: str, chunk_size: int) -> list[str]:
    if not text or len(text) <= chunk_size:
        return [text] if text else []
    delimiters = ["。", "！", "？", "\n"]
    chunks = []
    start_index = 0
    while start_index < len(text):
        end_index = start_index + chunk_size
        if end_index >= len(text):
            chunks.append(text[start_index:])
            break

        best_split_pos = -1
        for delimiter in delimiters:
            position = text.rfind(delimiter, start_index, end_index)
            if position > best_split_pos:
                best_split_pos = position

        if best_split_pos != -1:
            chunks.append(text[start_index : best_split_pos + 1])
            start_index = best_split_pos + 1
        else:
            chunks.append(text[start_index:end_index])
            start_index = end_index
    return [chunk for chunk in chunks if chunk.strip()]


def get_last_sentence(text: str) -> str:
    if not text:
        return ""
    sentences = re.split(r"(?<=[。？！\n])", text.strip())
    return sentences[-1].strip() if sentences else ""


def ensure_markdown_heading(text: str) -> str:
    normalized = text.strip()
    if not normalized:
        return "# 学术笔记"
    if normalized.startswith("#"):
        return normalized
    return f"# 学术笔记\n\n{normalized}"


def build_calibration_status_message(opt_message: str, is_calibrated: bool) -> str:
    if is_calibrated:
        return f"转录完成，{opt_message}"
    if "跳过" in opt_message:
        return f"转录完成 ({opt_message.replace('校准已跳过', '校准服务')})"
    return f"转录完成，{opt_message.replace('校准失败', '但校准失败')}"


def transcribe_audio(
    uploaded_audio: UploadedAudio,
    services: ServiceContainer,
    cancel_event: Optional[object] = None,
) -> str:
    return services.s2t_client.transcribe(uploaded_audio, cancel_event=cancel_event)


def _parallel_map(
    items: list[dict[str, object]],
    worker: Callable[[dict[str, object], Optional[object]], dict[str, object]],
    *,
    max_workers: int,
    cancel_event: Optional[object] = None,
    progress_callback: Optional[ProgressCallback] = None,
    progress_start: int = 45,
    progress_end: int = 90,
) -> list[dict[str, object]]:
    if not items:
        return []

    results: list[Optional[dict[str, object]]] = [None] * len(items)
    completed_count = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_index = {
            executor.submit(worker, item, cancel_event): index for index, item in enumerate(items)
        }
        try:
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                if cancel_event and cancel_event.is_set():
                    raise OperationCancelled()

                try:
                    result = future.result()
                except OperationCancelled:
                    raise
                except Exception as exc:
                    result = {"status": "error", "message": f"未知错误: {exc}"}

                results[index] = result
                completed_count += 1

                if progress_callback:
                    progress = progress_start + int(
                        (completed_count / len(items)) * (progress_end - progress_start)
                    )
                    progress_callback(
                        "OPTIMIZING_CHUNK",
                        f"正在校准文本块 ({completed_count}/{len(items)})...",
                        progress,
                    )

                if result.get("status") == "cancelled":
                    raise OperationCancelled()
        finally:
            if cancel_event and cancel_event.is_set():
                for future in future_to_index:
                    future.cancel()

    return [result or {"status": "error", "message": "任务未返回结果"} for result in results]


def _build_calibration_messages(text_chunk: str, context_sentence: Optional[str]) -> list[dict[str, str]]:
    if context_sentence:
        user_content = (
            "为了保持上下文连贯，这是紧接在当前文本之前的最后一句话：\n"
            "---CONTEXT---\n"
            f"{context_sentence}\n"
            "---END CONTEXT---\n\n"
            "请仅校准并返回以下这段新的文本，不要在你的回答中重复上面的上下文内容：\n"
            "---TEXT TO CALIBRATE---\n"
            f"{text_chunk}\n"
            "---END TEXT---"
        )
    else:
        user_content = text_chunk

    return [
        {"role": "system", "content": HARDCODED_OPTIMIZATION_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _optimize_chunk_with_retry(
    chunk_data: dict[str, object],
    services: ServiceContainer,
    cancel_event: Optional[object] = None,
) -> dict[str, object]:
    text_chunk = str(chunk_data["text"])
    context_sentence = chunk_data.get("context")
    chunk_index = int(chunk_data.get("index", 1))
    total_chunks = int(chunk_data.get("total", 1))
    log_prefix = f"块 {chunk_index}/{total_chunks}"

    try:
        print(f"校准{log_prefix}...")
        content = services.chat_client.complete(
            model=services.config.calibration_model,
            messages=_build_calibration_messages(text_chunk, context_sentence if isinstance(context_sentence, str) else None),
            temperature=0.1,
            empty_message="API返回空内容",
            feature_name="校准",
            cancel_event=cancel_event,
        )
        print(f"{log_prefix} 校准成功。")
        return {"status": "success", "content": content}
    except OperationCancelled:
        return {"status": "cancelled", "message": "操作已取消"}
    except AppError as exc:
        print(f"{log_prefix} 校准失败: {exc.message}")
        return {"status": "error", "message": exc.message}


def perform_text_optimization(
    raw_text_to_optimize: str,
    services: ServiceContainer,
    *,
    progress_callback: Optional[ProgressCallback] = None,
    cancel_event: Optional[object] = None,
) -> tuple[str, str, bool]:
    missing = services.config.get_missing_opt_parts(services.config.calibration_model)
    if missing:
        opt_status_message = services.config.get_calibration_skip_message()
        print(f"OPT API 配置不完整，跳过文本优化。原因: {opt_status_message}")
        return raw_text_to_optimize, opt_status_message, False

    if len(raw_text_to_optimize) <= services.config.chunk_processing_threshold:
        result = _optimize_chunk_with_retry(
            {"text": raw_text_to_optimize, "index": 1, "total": 1},
            services,
            cancel_event=cancel_event,
        )
        if result["status"] == "success":
            return str(result["content"]), "校准成功！", True
        if result["status"] == "cancelled":
            raise OperationCancelled()
        return raw_text_to_optimize, f"校准失败 ({result['message']})", False

    chunks = split_text_intelligently(raw_text_to_optimize, services.config.chunk_target_size)
    total_chunks = len(chunks)
    print(f"文本过长({len(raw_text_to_optimize)}字)，启动分块并发校准 (共 {total_chunks} 块)...")
    tasks = [
        {
            "text": chunk,
            "context": get_last_sentence(chunks[index - 1]) if index > 0 else None,
            "index": index + 1,
            "total": total_chunks,
        }
        for index, chunk in enumerate(chunks)
    ]

    processed_results = _parallel_map(
        tasks,
        lambda item, event: _optimize_chunk_with_retry(item, services, event),
        max_workers=services.config.max_concurrent_workers,
        cancel_event=cancel_event,
        progress_callback=progress_callback,
    )

    failed_chunks = [result for result in processed_results if result["status"] == "error"]
    if failed_chunks:
        first_error_message = str(failed_chunks[0]["message"])
        print(
            f"校准过程中有 {len(failed_chunks)}/{total_chunks} 个块处理失败，回退到原始文本。首个失败原因: {first_error_message}"
        )
        return raw_text_to_optimize, f"校准失败 ({first_error_message})", False

    full_optimized_text = "".join(str(result["content"]) for result in processed_results)
    print(f"所有 {total_chunks} 个块均已成功校准并合并。")
    return full_optimized_text, "校准成功！", True


def _map_summary_chunk(
    task: dict[str, object],
    services: ServiceContainer,
    cancel_event: Optional[object] = None,
) -> dict[str, object]:
    try:
        content = services.chat_client.complete(
            model=services.config.summary_model,
            messages=[
                {"role": "system", "content": PROMPT_SUMMARY_MAP},
                {"role": "user", "content": str(task["text"])},
            ],
            temperature=0.1,
            empty_message="API为Map阶段返回空内容",
            feature_name="摘要生成",
            cancel_event=cancel_event,
        )
        return {"status": "success", "content": content}
    except OperationCancelled:
        return {"status": "cancelled", "message": "操作已取消"}
    except AppError as exc:
        return {"status": "error", "message": exc.message}


def _group_fragments(fragments: list[str], chunk_size: int) -> list[list[str]]:
    groups: list[list[str]] = []
    current_group: list[str] = []
    current_size = 0
    for fragment in fragments:
        fragment_size = len(fragment) + 2
        if current_group and current_size + fragment_size > chunk_size:
            groups.append(current_group)
            current_group = [fragment]
            current_size = fragment_size
        else:
            current_group.append(fragment)
            current_size += fragment_size
    if current_group:
        groups.append(current_group)
    return groups


def _reduce_fragments(
    fragments: list[str],
    *,
    services: ServiceContainer,
    system_prompt: str,
    feature_name: str,
    user_content_builder: Callable[[str], str] | None = None,
    cancel_event: Optional[object] = None,
) -> str:
    if not fragments:
        raise ValidationError("待处理文本为空或分块失败")

    current = [fragment for fragment in fragments if fragment.strip()]
    round_count = 0
    max_rounds = max(4, len(current) * 2)

    while len(current) > 1 or len("\n\n".join(current)) > services.config.chunk_processing_threshold:
        round_count += 1
        if round_count > max_rounds:
            current = ["\n\n".join(current)]

        if len(current) == 1:
            reduced = services.chat_client.complete(
                model=services.config.summary_model if feature_name == "摘要生成" else services.config.notes_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": user_content_builder(current[0])
                        if user_content_builder
                        else current[0],
                    },
                ],
                temperature=0.2,
                empty_message="Reduce阶段返回空内容",
                feature_name=feature_name,
                cancel_event=cancel_event,
            )
            if reduced.strip() == current[0].strip() or len(reduced) >= len(current[0]):
                return reduced
            current = [reduced]
            continue

        next_round = []
        groups = _group_fragments(current, services.config.chunk_target_size)
        if len(groups) == len(current) and len(current) > 1:
            midpoint = max(1, (len(current) + 1) // 2)
            groups = [current[:midpoint], current[midpoint:]]
            groups = [group for group in groups if group]

        for group in groups:
            if cancel_event and cancel_event.is_set():
                raise OperationCancelled()
            reduced = services.chat_client.complete(
                model=services.config.summary_model if feature_name == "摘要生成" else services.config.notes_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": user_content_builder("\n\n".join(group))
                        if user_content_builder
                        else "\n\n".join(group),
                    },
                ],
                temperature=0.2,
                empty_message="Reduce阶段返回空内容",
                feature_name=feature_name,
                cancel_event=cancel_event,
            )
            next_round.append(reduced)

        if len(next_round) >= len(current):
            current = ["\n\n".join(next_round)]
        else:
            current = next_round
    return current[0]


def perform_summarization(
    text_to_summarize: str,
    services: ServiceContainer,
    *,
    cancel_event: Optional[object] = None,
) -> str:
    if not isinstance(text_to_summarize, str) or not text_to_summarize.strip():
        raise ValidationError("待总结的文本不能为空")

    services.chat_client.ensure_configured(services.config.summary_model, "摘要生成")
    chunks = split_text_intelligently(text_to_summarize, services.config.chunk_target_size)
    tasks = [{"text": chunk} for chunk in chunks]
    map_results = _parallel_map(
        tasks,
        lambda item, event: _map_summary_chunk(item, services, event),
        max_workers=services.config.max_concurrent_workers,
        cancel_event=cancel_event,
    )

    failed_chunks = [result for result in map_results if result["status"] == "error"]
    if failed_chunks:
        raise UpstreamServiceError(f"提取要点失败 ({failed_chunks[0]['message']})", code="summary_map_failed")

    points = [str(result["content"]) for result in map_results]
    return _reduce_fragments(
        points,
        services=services,
        system_prompt=PROMPT_SUMMARY_REDUCE,
        feature_name="摘要生成",
        cancel_event=cancel_event,
    )


def _notes_input(text: str) -> str:
    return f"<待处理文本>\n{text.strip()}\n</待处理文本>"


def _normalize_note_fragment(text: str) -> str:
    lines = [line.rstrip() for line in text.strip().splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].lstrip().startswith("# "):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip()


def _prepare_notes_for_final_merge(fragments: list[str]) -> str:
    normalized_fragments = []
    for fragment in fragments:
        normalized = _normalize_note_fragment(fragment)
        if not normalized:
            continue
        normalized_fragments.append(normalized)
    return "\n\n".join(normalized_fragments).strip()


def _map_notes_chunk(
    task: dict[str, object],
    services: ServiceContainer,
    cancel_event: Optional[object] = None,
) -> dict[str, object]:
    try:
        content = services.chat_client.complete(
            model=services.config.notes_model,
            messages=[
                {"role": "system", "content": PROMPT_GENERATE_NOTES},
                {"role": "user", "content": _notes_input(str(task["text"]))},
            ],
            temperature=0.2,
            empty_message="笔记分块生成返回空内容",
            feature_name="笔记生成",
            cancel_event=cancel_event,
        )
        return {"status": "success", "content": content}
    except OperationCancelled:
        return {"status": "cancelled", "message": "操作已取消"}
    except AppError as exc:
        return {"status": "error", "message": exc.message}


def perform_notes_generation(
    text_to_process: str,
    services: ServiceContainer,
    *,
    cancel_event: Optional[object] = None,
) -> str:
    if not isinstance(text_to_process, str) or not text_to_process.strip():
        raise ValidationError("待处理的文本不能为空")

    services.chat_client.ensure_configured(services.config.notes_model, "笔记生成")

    if len(text_to_process) <= services.config.chunk_processing_threshold:
        content = services.chat_client.complete(
            model=services.config.notes_model,
            messages=[
                {"role": "system", "content": PROMPT_GENERATE_NOTES},
                {"role": "user", "content": _notes_input(text_to_process)},
            ],
            temperature=0.2,
            empty_message="API返回空内容",
            feature_name="笔记生成",
            cancel_event=cancel_event,
        )
        return ensure_markdown_heading(content)

    chunks = split_text_intelligently(text_to_process, services.config.chunk_target_size)
    tasks = [{"text": chunk} for chunk in chunks]
    map_results = _parallel_map(
        tasks,
        lambda item, event: _map_notes_chunk(item, services, event),
        max_workers=services.config.max_concurrent_workers,
        cancel_event=cancel_event,
    )
    failed_chunks = [result for result in map_results if result["status"] == "error"]
    if failed_chunks:
        raise UpstreamServiceError(
            f"笔记分块生成失败 ({failed_chunks[0]['message']})",
            code="notes_map_failed",
        )

    merged_input = _prepare_notes_for_final_merge(
        [str(result["content"]) for result in map_results]
    )
    if not merged_input:
        raise UpstreamServiceError("笔记分块生成返回空内容", code="notes_map_empty")

    merged_notes = services.chat_client.complete(
        model=services.config.notes_model,
        messages=[
            {"role": "system", "content": PROMPT_GENERATE_NOTES},
            {"role": "user", "content": _notes_input(merged_input)},
        ],
        temperature=0.2,
        empty_message="笔记最终整合返回空内容",
        feature_name="笔记生成",
        cancel_event=cancel_event,
    )
    return ensure_markdown_heading(merged_notes)
