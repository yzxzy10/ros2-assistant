# ROS2 学习助手 —— 最小镜像（纯标准库，无需装依赖）
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    ROS2_DATA_DIR=/data

COPY . /app
RUN mkdir -p /data

# 会话、画像库、归档全部落在 /data —— 必须挂卷，否则容器重建即丢
VOLUME ["/data"]
EXPOSE 8000

# 密钥与令牌通过环境变量/.env 注入，镜像内不含任何凭据
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status==200 else 1)"

CMD ["python", "-X", "utf8", "server.py"]
