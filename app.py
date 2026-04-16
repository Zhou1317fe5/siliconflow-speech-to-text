from waitress import serve

from speech_to_text import AppConfig, create_app


def main() -> None:
    config = AppConfig.from_env()
    for message in config.startup_messages():
        print(message)

    app = create_app(config)
    print("服务器正在启动，监听 http://0.0.0.0:5000")
    serve(app, host="0.0.0.0", port=5000, threads=config.waitress_threads)


if __name__ == "__main__":
    main()
