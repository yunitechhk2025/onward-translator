# -*- coding: utf-8 -*-
"""下载 faster-whisper-small 模型到 models/whisper-small（直连失败自动切 hf-mirror）"""
import os, sys
from pathlib import Path
from huggingface_hub import snapshot_download

BASE = Path(__file__).resolve().parent
dest = BASE / "models" / "whisper-small"

try:
    snapshot_download("Systran/faster-whisper-small", local_dir=str(dest))
    print("DOWNLOADED_DIRECT")
except Exception as e:
    print(f"direct failed: {e}", file=sys.stderr)
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    snapshot_download("Systran/faster-whisper-small", local_dir=str(dest))
    print("DOWNLOADED_MIRROR")
