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
nano config.json   # 填阿里云 AccessKey + admin_password
docker compose up -d --build
```

首次启动会自动下载 Whisper 模型（约 460MB）到 `./models`，之后复用。访问：`http://服务器IP:8010`

> 与同机富润康客服（8000）错开，本服务默认映射 **8010**。

### C. GitHub Secrets（自动部署 + 可选翻译密钥）

仓库 → Settings → Secrets and variables → Actions，新增：

| Secret | 含义 | 必填 |
|---|---|---|
| `ECS_HOST` | 服务器公网 IP | 是 |
| `ECS_USER` | SSH 用户名（常为 `root`） | 是 |
| `ECS_PASSWORD` | SSH 密码 | 是 |
| `ALIYUN_ACCESS_KEY_ID` | 阿里云翻译 AccessKeyId | 否 |
| `ALIYUN_ACCESS_KEY_SECRET` | 阿里云翻译 AccessKeySecret | 否 |

部署时 Actions 会把阿里云密钥写入服务器 `~/onward-translator/.env`（不进 Git）。**未配置这两项时，翻译自动降级 Google**。

之后每次 `git push origin main`，Actions 工作流 `Deploy to Aliyun ECS` 会自动更新线上容器。也可在 Actions 页手动 Run workflow。

> `config.json` / `.env` / `jobs/` / `models/` 已进 `.gitignore`，`git pull` 不会把密钥提交进仓库。

### D. 二级域名 + HTTPS（与富润康同机 Nginx）

| 访问方式 | 例子 | 区别 |
|---|---|---|
| IP + 端口 | `http://47.86.98.52:8010` | 最快上线；要记端口；无 HTTPS；不宜给客人 |
| 二级域名 + HTTPS | `https://translate.xxx.com` | 好记、有锁、可分享；同机多站靠域名分流 |

步骤：

1. DNS 添一条 **A 记录**：主机如 `translate` → 值 `47.86.98.52`
2. 安全组已放行 **80 / 443**（富润康配域名时一般已开）
3. 等 DNS 生效后，GitHub → Actions → **Setup Nginx site + HTTPS** → Run workflow  
   - `domain`：你的二级域名  
   - `upstream_port`：`8010`  
   - 勾选申请 HTTPS  

不会改动已有的 `ai.fuyunhon.com` 等站点；Nginx 按 `Host` 分别反代到 8000 / 8010。

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

正式提供服務建議接入雲廠商機器翻譯（本倉庫已接**阿里雲**）。

### 已接入阿里雲機器翻譯（主通道，可選）

推薦把密鑰放進 **GitHub Secrets**（見上文 `ALIYUN_ACCESS_KEY_ID` / `ALIYUN_ACCESS_KEY_SECRET`），部署時自動寫入服務器 `.env`。

- **不配密鑰**：自動降級 Google，再失敗保留原文（粵語聲兜底）
- **配了密鑰**：走阿里雲通用版（有每月免費字符額度）
- 也可本地在 `.env` / `config.json` 填寫，優先級：環境變量 > config.json

## 已知限制

- 視頻時長建議 ≤ 15 分鐘（更長會佔用較多 CPU 時間）
- YouTube 對數據中心 IP 有風控，雲上部署可能需要代理出口
- 原視頻若自帶硬字幕，印尼語字幕會疊加在下方，不衝突但需注意遮擋

## 路線圖

- [ ] 他加祿語（菲律賓）、泰語支持（edge-tts 已有對應音色）
- [ ] 做菜詞彙表定制（常用調料、工序術語優先翻譯）
- [ ] 微信小程序 / WhatsApp Bot 直接發送
