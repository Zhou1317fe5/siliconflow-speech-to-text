# 使用官方的 Python 运行时作为基础镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 将 requirements.txt 复制到工作目录
COPY requirements.txt .

# 安装 Python 依赖
# 使用 --no-cache-dir 减小镜像大小
RUN pip install --no-cache-dir -r requirements.txt

# 将主应用脚本复制到工作目录
COPY app.py . 

# 将后端应用包复制到容器内
COPY speech_to_text/ /app/speech_to_text/

# 将模板文件复制到容器内的 templates 目录
COPY templates/ /app/templates/

# 将静态文件复制到容器内的 static 目录
COPY static/ /app/static/

# 暴露应用运行的端口
EXPOSE 5000

# 运行应用
CMD ["python", "app.py"]
