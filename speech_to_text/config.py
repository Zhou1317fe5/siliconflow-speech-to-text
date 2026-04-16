from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Optional

from .errors import ConfigurationError


def _clean_optional(value: Optional[object]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_positive_int(raw_value: Optional[object], env_name: str, default: int) -> int:
    if raw_value is None or str(raw_value).strip() == "":
        return default
    try:
        value = int(str(raw_value).strip())
    except ValueError as exc:
        raise ConfigurationError(f"环境变量 {env_name} 必须是正整数") from exc
    if value <= 0:
        raise ConfigurationError(f"环境变量 {env_name} 必须大于 0")
    return value


def _is_http_url(value: Optional[str]) -> bool:
    return bool(value and value.startswith(("http://", "https://")))


@dataclass(frozen=True)
class AppConfig:
    s2t_api_url: str = "https://api.siliconflow.cn/v1/audio/transcriptions"
    s2t_api_key: Optional[str] = None
    s2t_model: str = "FunAudioLLM/SenseVoiceSmall"

    opt_api_url: str = "https://api.openai.com/v1/chat/completions"
    opt_api_key: Optional[str] = None
    opt_model: Optional[str] = None

    calibration_model: Optional[str] = None
    summary_model: Optional[str] = None
    notes_model: Optional[str] = None

    chunk_target_size: int = 5000
    chunk_processing_threshold: int = 5500
    max_concurrent_workers: int = 3
    retry_attempts: int = 3

    api_access_token: Optional[str] = None
    model_calibrate: str = "s2t-calibrated"
    model_summarize: str = "s2t-summarized"

    max_upload_size_mb: int = 50
    upstream_timeout_seconds: int = 300

    @property
    def max_upload_size_bytes(self) -> int:
        return self.max_upload_size_mb * 1024 * 1024

    @classmethod
    def from_env(cls) -> "AppConfig":
        import os

        env = os.environ
        opt_model = _clean_optional(env.get("OPT_MODEL"))
        return cls(
            s2t_api_url=_clean_optional(env.get("S2T_API_URL")) or cls.s2t_api_url,
            s2t_api_key=_clean_optional(env.get("S2T_API_KEY")),
            s2t_model=_clean_optional(env.get("S2T_MODEL")) or cls.s2t_model,
            opt_api_url=_clean_optional(env.get("OPT_API_URL")) or cls.opt_api_url,
            opt_api_key=_clean_optional(env.get("OPT_API_KEY")),
            opt_model=opt_model,
            calibration_model=_clean_optional(env.get("CALIBRATION_MODEL")) or opt_model,
            summary_model=_clean_optional(env.get("SUMMARY_MODEL")) or opt_model,
            notes_model=_clean_optional(env.get("NOTES_MODEL")) or opt_model,
            chunk_target_size=_parse_positive_int(
                env.get("CHUNK_TARGET_SIZE"), "CHUNK_TARGET_SIZE", cls.chunk_target_size
            ),
            chunk_processing_threshold=_parse_positive_int(
                env.get("CHUNK_PROCESSING_THRESHOLD"),
                "CHUNK_PROCESSING_THRESHOLD",
                cls.chunk_processing_threshold,
            ),
            max_concurrent_workers=_parse_positive_int(
                env.get("MAX_CONCURRENT_WORKERS"),
                "MAX_CONCURRENT_WORKERS",
                cls.max_concurrent_workers,
            ),
            retry_attempts=_parse_positive_int(
                env.get("RETRY_ATTEMPTS"), "RETRY_ATTEMPTS", cls.retry_attempts
            ),
            api_access_token=_clean_optional(env.get("API_ACCESS_TOKEN")),
            model_calibrate=_clean_optional(env.get("MODEL_CALIBRATE")) or cls.model_calibrate,
            model_summarize=_clean_optional(env.get("MODEL_SUMMARIZE")) or cls.model_summarize,
            max_upload_size_mb=_parse_positive_int(
                env.get("MAX_UPLOAD_SIZE_MB"), "MAX_UPLOAD_SIZE_MB", cls.max_upload_size_mb
            ),
            upstream_timeout_seconds=_parse_positive_int(
                env.get("UPSTREAM_TIMEOUT_SECONDS"),
                "UPSTREAM_TIMEOUT_SECONDS",
                cls.upstream_timeout_seconds,
            ),
        )

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> "AppConfig":
        env_like = {key: mapping.get(key) for key in mapping}
        opt_model = _clean_optional(env_like.get("OPT_MODEL"))
        return cls(
            s2t_api_url=_clean_optional(env_like.get("S2T_API_URL")) or cls.s2t_api_url,
            s2t_api_key=_clean_optional(env_like.get("S2T_API_KEY")),
            s2t_model=_clean_optional(env_like.get("S2T_MODEL")) or cls.s2t_model,
            opt_api_url=_clean_optional(env_like.get("OPT_API_URL")) or cls.opt_api_url,
            opt_api_key=_clean_optional(env_like.get("OPT_API_KEY")),
            opt_model=opt_model,
            calibration_model=_clean_optional(env_like.get("CALIBRATION_MODEL")) or opt_model,
            summary_model=_clean_optional(env_like.get("SUMMARY_MODEL")) or opt_model,
            notes_model=_clean_optional(env_like.get("NOTES_MODEL")) or opt_model,
            chunk_target_size=_parse_positive_int(
                env_like.get("CHUNK_TARGET_SIZE"), "CHUNK_TARGET_SIZE", cls.chunk_target_size
            ),
            chunk_processing_threshold=_parse_positive_int(
                env_like.get("CHUNK_PROCESSING_THRESHOLD"),
                "CHUNK_PROCESSING_THRESHOLD",
                cls.chunk_processing_threshold,
            ),
            max_concurrent_workers=_parse_positive_int(
                env_like.get("MAX_CONCURRENT_WORKERS"),
                "MAX_CONCURRENT_WORKERS",
                cls.max_concurrent_workers,
            ),
            retry_attempts=_parse_positive_int(
                env_like.get("RETRY_ATTEMPTS"), "RETRY_ATTEMPTS", cls.retry_attempts
            ),
            api_access_token=_clean_optional(env_like.get("API_ACCESS_TOKEN")),
            model_calibrate=_clean_optional(env_like.get("MODEL_CALIBRATE")) or cls.model_calibrate,
            model_summarize=_clean_optional(env_like.get("MODEL_SUMMARIZE")) or cls.model_summarize,
            max_upload_size_mb=_parse_positive_int(
                env_like.get("MAX_UPLOAD_SIZE_MB"), "MAX_UPLOAD_SIZE_MB", cls.max_upload_size_mb
            ),
            upstream_timeout_seconds=_parse_positive_int(
                env_like.get("UPSTREAM_TIMEOUT_SECONDS"),
                "UPSTREAM_TIMEOUT_SECONDS",
                cls.upstream_timeout_seconds,
            ),
        )

    def with_overrides(self, **overrides: object) -> "AppConfig":
        return replace(self, **overrides)

    def is_opt_url_valid(self) -> bool:
        return _is_http_url(self.opt_api_url)

    def is_s2t_url_valid(self) -> bool:
        return _is_http_url(self.s2t_api_url)

    def get_missing_opt_parts(self, model: Optional[str]) -> list[str]:
        missing = []
        if not self.opt_api_key:
            missing.append("缺少API Key")
        if not self.is_opt_url_valid():
            missing.append("API URL无效")
        if not model:
            missing.append("缺少模型名称")
        return missing

    def get_calibration_skip_message(self) -> str:
        missing = self.get_missing_opt_parts(self.calibration_model)
        if not missing:
            return "校准成功！"
        if not self.opt_api_key and self.opt_api_url == self.__class__.opt_api_url and not self.calibration_model:
            return "校准已跳过 (服务未配置)"
        return f"校准已跳过 (服务配置不完整: {', '.join(missing)})"

    def validate_runtime(self) -> None:
        if not self.is_s2t_url_valid():
            raise ConfigurationError(f"S2T_API_URL 无效: {self.s2t_api_url}")
        if self.opt_api_key and not self.is_opt_url_valid():
            raise ConfigurationError(f"OPT_API_URL 无效: {self.opt_api_url}")

    def startup_messages(self) -> list[str]:
        messages = ["--- S2T 配置检查 ---"]
        if not self.s2t_api_key:
            messages.append("警告: 环境变量 S2T_API_KEY 未设置。")
        if not self.is_s2t_url_valid():
            messages.append(f"警告: 环境变量 S2T_API_URL 格式不正确: {self.s2t_api_url}")

        messages.append("\n--- OPT 配置检查 ---")
        if self.opt_api_key or self.opt_model:
            if not self.opt_api_key:
                messages.append("警告: 环境变量 OPT_API_KEY 未设置。文本优化功能将无法使用。")
            if self.opt_api_key and not self.is_opt_url_valid():
                messages.append(f"警告: 环境变量 OPT_API_URL ({self.opt_api_url}) 无效或格式不正确。")
            if self.opt_api_key and not self.opt_model:
                messages.append("警告: 已设置 OPT_API_KEY 但未设置 OPT_MODEL。")
            messages.append("\n--- 功能专用模型配置检查 ---")
            messages.append(
                f"✓ 校准功能使用模型: {self.calibration_model or '(未配置)'}"
            )
            messages.append(
                f"✓ 摘要功能使用模型: {self.summary_model or '(未配置)'}"
            )
            messages.append(
                f"✓ 笔记功能使用模型: {self.notes_model or '(未配置)'}"
            )
        else:
            messages.append("提示: OPT服务未配置，校准、总结和笔记生成功能将不可用。")

        messages.append("\n--- API 封装功能检查 ---")
        if not self.api_access_token:
            messages.append("警告: 环境变量 API_ACCESS_TOKEN 未设置或为空。API封装功能将无法通过认证。")
        else:
            messages.append("API封装功能已启用。")

        messages.append("\n--- 运行参数 ---")
        messages.append(f"上传文件上限: {self.max_upload_size_mb} MB")
        messages.append(f"上游超时时间: {self.upstream_timeout_seconds} 秒")
        messages.append("--------------------")
        return messages
