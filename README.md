# 傭工語言橋 · 做菜視頻翻譯服務

把 YouTube / B站 的做菜視頻自動轉成**印尼語配音 + 印尼語字幕**的 MP4，
方便僱主發 WhatsApp / 微信給家中印尼外傭。

## 功能

- 貼上視頻連結 → 自動：下載 → 語音識別（Whisper AI）→ 翻譯 → 印尼語配音（微軟 Gadis 女聲）→ 燒錄大號字幕
- 輸出手機友好 MP4（720p、<25MB 可直接 WhatsApp 傳送）+ 獨立 .srt 字幕檔
- 網頁進度條實時顯示處理階段
- 支持斷點續傳（TTS 段落級重試），網絡波動不易整單失敗

## 目錄結構

```
onward-translator/
├── app.py                 # FastAPI 後端（核心流水線）
├── Dockerfile             # 容器鏡像（含 ffmpeg）
├── docker-compose.yml     # 一鍵部署
├── config.example.json    # 配置模板（複製為 config.json）
├── .github/workflows/     # push main → SSH 自動部署
├── templates/
│   └── index.html         # 客人使用的網頁界面
└── README.md
```

## Docker 部署（推薦上線）+ GitHub 自動更新

與既有阿里雲 ECS 項目同一套路：**push 到 `main` → Actions SSH 登錄服務器 → `git pull` + `docker compose up -d --build`**。

### A. 本地準備並推到 GitHub

1. 建空倉庫（例如 `yunitechhk2025/onward-translator`），不要勾選 README。
2. 本機：

```bat
git init
git add .
git commit -m "chore: docker + github actions deploy"
git branch -M main
git remote add origin https://github.com/<org>/<repo>.git
git push -u origin main
```

### B. 服務器首次部署（只做一次）

```bash
# 需已安装 Docker / Docker Compose / git
cd ~
git clone https://github.com/<org>/<repo>.git onward-translator
cd onward-translator
cp config.example.json config.json
nano config.json   # 填腾讯云密钥 + admin_password
docker compose up -d --build
```

首次启动会自动下载 Whisper 模型（约 460MB）到 `./models`，之后复用。访问：`http://服务器IP:8010`

> 与同机富润康客服（8000）错开，本服务默认映射 **8010**。

### C. GitHub Secrets（自动部署）

仓库 → Settings → Secrets and variables → Actions，新增：

| Secret | 含义 |
|---|---|
| `ECS_HOST` | 服务器公网 IP |
| `ECS_USER` | SSH 用户名（常为 `root`） |
| `ECS_PASSWORD` | SSH 密码 |

之后每次 `git push origin main`，Actions 工作流 `Deploy to Aliyun ECS` 会自动更新线上容器。也可在 Actions 页手动 Run workflow。

> `config.json` / `jobs/` / `models/` 已进 `.gitignore`，`git pull` 不会覆盖服务器上的密钥与模型缓存。

## 本地部署（Windows，推薦給中介自用）

1. 安裝 [Python 3.10+](https://www.python.org/downloads/)（勾選 Add to PATH）與 [FFmpeg](https://ffmpeg.org/download.html)（解壓後把 bin 加入 PATH）
2. 開啟命令提示字元：

```bat
cd video-translator
pip install fastapi uvicorn edge-tts faster-whisper deep-translator yt-dlp

:: 下載 AI 語音識別模型（約460MB，只需一次）
pip install huggingface_hub
python -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-small', local_dir='models/whisper-small')"

:: 啟動
set WHISPER_MODEL=models/whisper-small
python app.py
```

3. 瀏覽器打開 <http://localhost:8000>，貼上 YouTube 連結即可

> 中國大陸網絡環境：YouTube 需自行保證可達；模型下載可改用鏡像
> `set HF_ENDPOINT=https://hf-mirror.com` 或從魔搭 `pengzhendong/faster-whisper-small` 下載。

## 雲端部署（給客人自助用）

| 方案 | 月費大約 | 適合 |
|---|---|---|
| 家裡舊電腦 / 辦公室一台機 24 小時跑 | 0 | 起步、用量小 |
| 騰訊雲 / 阿里雲輕量服務器（2核4G） | HK$30–60 | 穩定對外提供 |
| Render / Railway（容器部署） | US$7–25 | 免運維 |

建議：先在自己電腦跑通，累積 20–30 個視頻「需求樣本」後再上雲。

## 翻譯質量提升（可選）

沙箱演示中翻譯走了降級通道（Google 免費接口被牆）。正式提供服務建議接入：

- **DeepL API Free**（每月 50 萬字免費，質量最好）
- 騰訊雲 / 百度翻譯 API（國內直連、有免費額度）

在 `app.py` 的 `translate_segments()` 裡換成你的 API key 即可，接口已預留。

### 已接入騰訊雲機器翻譯（主通道）

- 在項目根目錄的 `config.json` 填入 `tencent_secret_id` / `tencent_secret_key`（每月 500 萬字符免費）
- **密鑰只在 `config.json`，不寫入源碼；打包分享源碼時務必刪除該文件**
- 騰訊雲不可用時自動降級到 Google，再失敗保留原文（粵語聲兜底）

## 已知限制

- 視頻時長建議 ≤ 15 分鐘（更長會佔用較多 CPU 時間）
- YouTube 對數據中心 IP 有風控，雲上部署可能需要代理出口
- 原視頻若自帶硬字幕，印尼語字幕會疊加在下方，不衝突但需注意遮擋

## 路線圖

- [ ] 他加祿語（菲律賓）、泰語支持（edge-tts 已有對應音色）
- [ ] 做菜詞彙表定制（常用調料、工序術語優先翻譯）
- [ ] 微信小程序 / WhatsApp Bot 直接發送
