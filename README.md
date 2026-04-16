# Speech-To-Text 音频转录校准工具

## 项目简介

一个基于 Flask 的音频转录与文本优化服务，支持：

- 音频转文字
- 转录文本校准
- 长文本摘要生成
- 学术笔记生成
- OpenAI 兼容音频转录接口封装

项目提供 Web UI 和 HTTP API 两套入口，适合个人使用，也适合部署成内部服务。

## 功能特点

- **音频转录**：上传音频文件并调用上游语音识别 API
- **文本校准**：消除口语化、修正错字，长文本自动分块并发处理
- **量子速读**：Map-Reduce 架构生成长文本摘要
- **学术笔记生成**：保留原始笔记 prompt，支持长文本分块后再做一次最终整合
- **流式进度反馈**：Web UI 使用 `/api/transcribe-stream` 实时显示处理进度
- **可配置模型**：可分别配置校准、摘要、笔记模型
- **上传保护**：支持上传文件大小上限和上游超时配置
- **OpenAI 兼容 API**：支持 `/v1/models` 和 `/v1/audio/transcriptions`

## 当前目录结构

```text
.
├── app.py
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt
├── speech_to_text/
│   ├── app.py
│   ├── clients.py
│   ├── config.py
│   ├── errors.py
│   ├── prompts.py
│   ├── routes.py
│   ├── uploads.py
│   └── workflows.py
├── static/
├── templates/
└── tests/
```

## 接口说明

### Web UI

- `GET /`：页面入口
- `POST /api/transcribe`：普通转录接口
- `POST /api/transcribe-stream`：带 SSE 进度流的转录接口
- `POST /api/recalibrate`：对已有原始转录文本重新校准
- `POST /api/summarize`：生成摘要
- `POST /api/generatenote`：生成学术笔记

### OpenAI 兼容接口

- `GET /v1/models`
- `POST /v1/audio/transcriptions`

支持的模型名：

- `s2t-calibrated`
- `s2t-summarized`

## 环境变量

### 语音转录

- `S2T_API_URL`
  默认值：`https://api.siliconflow.cn/v1/audio/transcriptions`
- `S2T_API_KEY`
  必填，语音识别服务密钥
- `S2T_MODEL`
  默认值：`FunAudioLLM/SenseVoiceSmall`

### 文本优化

- `OPT_API_URL`
  默认值：`https://api.openai.com/v1/chat/completions`
- `OPT_API_KEY`
  可选；不配置时会跳过校准、摘要和笔记功能
- `OPT_MODEL`
  默认优化模型

### 专用模型

- `CALIBRATION_MODEL`
- `SUMMARY_MODEL`
- `NOTES_MODEL`

如果不单独配置，默认回退到 `OPT_MODEL`。

### API 封装

- `API_ACCESS_TOKEN`
  启用 `/v1/*` 路由时使用的 Bearer Token


## 本地运行

安装依赖：

```bash
python -m pip install -r requirements.txt
```

启动服务：

```bash
python app.py
```

默认监听：

```text
http://0.0.0.0:5000
```

## Docker 部署

构建镜像：

```bash
docker build -t speech-to-text .
```

运行容器：

```bash
docker run -d \
  --name speech-to-text \
  -p 5000:5000 \
  -e S2T_API_KEY=your-speech-to-text-api-key \
  -e OPT_API_KEY=your-text-optimizing-api-key \
  -e OPT_MODEL=your-default-model \
  -e CALIBRATION_MODEL=your-calibration-model \
  -e SUMMARY_MODEL=your-summary-model \
  -e NOTES_MODEL=your-notes-model \
  -e API_ACCESS_TOKEN=your-api-auth-key \
  -e MAX_UPLOAD_SIZE_MB=50 \
  -e UPSTREAM_TIMEOUT_SECONDS=300 \
  speech-to-text:latest
```

## Docker Compose 示例

```yaml
version: '3.8'
services:
  speech-to-text:
    image: speech-to-text:latest
    container_name: speech-to-text
    environment:
      # === 语音转录配置 ===
      # 语音转录 API 地址（可选），默认: https://api.siliconflow.cn/v1/audio/transcriptions
      - S2T_API_URL=https://api.siliconflow.cn/v1/audio/transcriptions
      # 语音转录 API Key（必需）
      - S2T_API_KEY=your-speech-to-text-api-key
      # 语音转录模型（可选），默认: FunAudioLLM/SenseVoiceSmall
      - S2T_MODEL=FunAudioLLM/SenseVoiceSmall

      # === 文本优化配置 ===
      # 文本优化 API 地址（可选），默认: https://api.openai.com/v1/chat/completions
      - OPT_API_URL=https://api.openai.com/v1/chat/completions
      # 文本优化 API Key（可选，不配置则跳过校准、摘要、笔记功能）
      - OPT_API_KEY=your-text-optimizing-api-key
      # 默认优化模型（可选）
      - OPT_MODEL=your-default-model

      # === 专用模型配置 ===
      # 文本校准专用模型（可选，优先于 OPT_MODEL）
      - CALIBRATION_MODEL=your-calibration-model
      # 摘要生成专用模型（可选，优先于 OPT_MODEL）
      - SUMMARY_MODEL=your-summary-model
      # 笔记生成专用模型（可选，优先于 OPT_MODEL）
      - NOTES_MODEL=your-notes-model

      # === API 封装功能配置 ===
      # OpenAI 兼容 API 的认证密钥（可选，启用 API 封装功能时需要）
      - API_ACCESS_TOKEN=your-api-auth-key

      # === 运行参数配置 ===
      # 上传文件大小上限（MB，可选），默认: 50
      - MAX_UPLOAD_SIZE_MB=50
      # 上游请求超时时间（秒，可选），默认: 300
      - UPSTREAM_TIMEOUT_SECONDS=300
    ports:
      - "your-port:5000"
```

启动：

```bash
docker compose up -d --build
```

查看日志：

```bash
docker compose logs --tail=200
```

对于 `docker compose`：

```bash
docker compose up -d --build
```

## 测试

安装开发依赖：

```bash
python -m pip install -r requirements-dev.txt
```

运行测试：

```bash
pytest -q
```

## 技术栈

- 后端：Python / Flask / Waitress
- 前端：HTML / CSS / JavaScript
- HTTP 客户端：requests

## 许可证

MIT License
