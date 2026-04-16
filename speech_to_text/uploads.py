from __future__ import annotations

import os
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile
from typing import BinaryIO

from werkzeug.datastructures import FileStorage

from .config import AppConfig
from .errors import UploadTooLargeError, ValidationError

READ_CHUNK_SIZE = 1024 * 1024
SPOOL_MAX_MEMORY = 1024 * 1024


@dataclass
class UploadedAudio:
    filename: str
    mimetype: str
    size: int
    stream: BinaryIO

    def to_requests_file(self) -> tuple[str, BinaryIO, str]:
        self.stream.seek(0)
        return (self.filename, self.stream, self.mimetype)

    def close(self) -> None:
        self.stream.close()


def prepare_uploaded_audio(file_storage: FileStorage, config: AppConfig) -> UploadedAudio:
    filename = os.path.basename(file_storage.filename or "").strip() or "audio"
    mimetype = file_storage.mimetype or "application/octet-stream"
    source = file_storage.stream
    if hasattr(source, "seek"):
        source.seek(0)

    temp_file = SpooledTemporaryFile(max_size=SPOOL_MAX_MEMORY)
    total_size = 0
    try:
        while True:
            chunk = source.read(READ_CHUNK_SIZE)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > config.max_upload_size_bytes:
                raise UploadTooLargeError(
                    f"上传文件超过限制，最大允许 {config.max_upload_size_mb} MB"
                )
            temp_file.write(chunk)

        if total_size == 0:
            raise ValidationError("上传的文件为空", code="empty_file")

        temp_file.seek(0)
        return UploadedAudio(
            filename=filename,
            mimetype=mimetype,
            size=total_size,
            stream=temp_file,
        )
    except Exception:
        temp_file.close()
        raise
