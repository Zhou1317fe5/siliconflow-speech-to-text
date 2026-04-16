from __future__ import annotations

from pathlib import Path
from typing import Mapping, Optional

from flask import Flask

from .clients import ChatCompletionClient, SpeechRecognitionClient
from .config import AppConfig
from .routes import register_routes
from .workflows import ServiceContainer


def create_app(config: Optional[object] = None) -> Flask:
    if config is None:
        app_config = AppConfig.from_env()
    elif isinstance(config, AppConfig):
        app_config = config
    elif isinstance(config, Mapping):
        app_config = AppConfig.from_mapping(config)
    else:
        raise TypeError("config 必须是 AppConfig、mapping 或 None")

    root_dir = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        static_folder=str(root_dir / "static"),
        static_url_path="",
        template_folder=str(root_dir / "templates"),
    )
    app.config["MAX_CONTENT_LENGTH"] = app_config.max_upload_size_bytes

    services = ServiceContainer(
        config=app_config,
        s2t_client=SpeechRecognitionClient(app_config),
        chat_client=ChatCompletionClient(app_config),
    )
    register_routes(app, services)
    return app
