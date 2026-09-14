#!/bin/sh
set -e

MODEL_DIR="${WHISPER_MODEL:-/app/models/whisper-small}"

# 站点反爬策略变化快，yt-dlp 必须保持最新，否则下载普遍失败
if [ "${SKIP_YTDLP_UPDATE:-0}" != "1" ]; then
  echo "==> 更新 yt-dlp ..."
  pip install --no-cache-dir --upgrade yt-dlp || echo "yt-dlp 更新失败，沿用镜像内版本"
fi

if [ ! -f "$MODEL_DIR/model.bin" ]; then
  echo "==> Whisper 模型不存在，开始下载到 $MODEL_DIR ..."
  mkdir -p "$(dirname "$MODEL_DIR")"
  python download_model.py
fi

exec "$@"
