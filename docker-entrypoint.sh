#!/bin/sh
set -e

MODEL_DIR="${WHISPER_MODEL:-/app/models/whisper-small}"

if [ ! -f "$MODEL_DIR/model.bin" ]; then
  echo "==> Whisper 模型不存在，开始下载到 $MODEL_DIR ..."
  mkdir -p "$(dirname "$MODEL_DIR")"
  python download_model.py
fi

exec "$@"
