FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WHISPER_MODEL=/app/models/whisper-small \
    HF_ENDPOINT=https://hf-mirror.com \
    PORT=8000

WORKDIR /app

# ffmpeg：抽音轨 / 混音 / 烧字幕；curl：B站下载兜底
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x /app/docker-entrypoint.sh

# 模型与任务目录挂载进来；构建时不塞 460MB 模型，缩短每次部署
VOLUME ["/app/models", "/app/jobs"]

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "app.py"]
