# -*- coding: utf-8 -*-
"""
YouTube / B站 做菜视频 → 印尼语配音 + 印尼语硬字幕
后端服务：贴链接即可自动处理，输出可下载的 MP4

运行：cd /workspace/video-translator && /usr/bin/python3 app.py
访问：http://localhost:8000
"""
import asyncio, json, os, re, shutil, subprocess, time, uuid, wave
from pathlib import Path
from fastapi import FastAPI, BackgroundTasks, HTTPException, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import numpy as np

BASE = Path(__file__).resolve().parent
JOBS = BASE / "jobs"
JOBS.mkdir(parents=True, exist_ok=True)
# 模型路径候选：发布环境与本地部署均可命中
_MODEL_CANDIDATES = [
    os.environ.get("WHISPER_MODEL"),
    str(BASE / "models" / "whisper-small"),
    "/root/.codebuddy/models/whisper-small",
]
MODEL_PATH = next((p for p in _MODEL_CANDIDATES if p and Path(p, "model.bin").exists()), None) or _MODEL_CANDIDATES[1]
SR = 44100
BG_VOL = 0.12

app = FastAPI(title="做菜视频印尼语翻译服务")


# ===================== 工具函数 =====================
def sh(cmd, timeout=1800):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout)


def get_bilibili_direct(url):
    """解析 B 站视频，拿到 MP4 直链与标题"""
    m = re.search(r"(BV[0-9A-Za-z]{10})", url)
    if not m:
        raise ValueError("无法识别 B 站视频 ID")
    bvid = m.group(1)
    ck = JOBS / "cookies.txt"
    if not ck.exists():
        sh(["curl", "-s", "-c", str(ck), "-o", "/dev/null", "-A", "Mozilla/5.0", "https://www.bilibili.com/"])
    api = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    info = json.loads(sh(["curl", "-s", "-m", "20", "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                          "-b", str(ck), api]).stdout)
    if info.get("code") != 0:
        raise ValueError("获取视频信息失败")
    d = info["data"]
    purl = (f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={d['cid']}"
            f"&qn=64&fnval=1&platform=html5")
    p = json.loads(sh(["curl", "-s", "-m", "20", "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                       "-b", str(ck), "-H", f"Referer: https://www.bilibili.com/video/{bvid}/", purl]).stdout)
    if p.get("code") != 0 or not p["data"].get("durl"):
        raise ValueError("获取播放地址失败")
    return p["data"]["durl"][0]["url"], d["title"], d.get("duration", 0)


def download_video(url, dest):
    """支持 B 站直链下载；YouTube 走 yt-dlp"""
    if "bilibili.com" in url or re.search(r"BV[0-9A-Za-z]{10}", url):
        direct, title, _ = get_bilibili_direct(url)
        ck = JOBS / "cookies.txt"
        sh(["curl", "-s", "-L", "-m", "900", "-o", str(dest), "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "-H", "Referer: https://www.bilibili.com/", "-b", str(ck), direct], timeout=1000)
        return title
    # YouTube（--no-playlist：链接带 &list= 时只下载当前视频，避免整列表下载/碰到私有视频整单失败）
    try:
        sh(["yt-dlp", "--no-warnings", "--no-playlist", "--socket-timeout", "20",
            "--retries", "10", "-f", "bv*[height<=720]+ba/b[height<=720]",
            "--merge-output-format", "mp4", "-o", str(dest), url], timeout=1800)
    except subprocess.CalledProcessError as e:
        detail = ((e.stderr or "") + (e.stdout or "")).lower()
        if any(k in detail for k in ("cookie", "sign in", "bot", "confirm you're", "http error 403", "429")):
            raise RuntimeError(
                "雲服務器 IP 被 YouTube 風控，無法直接下載。請改用「上傳視頻檔」或貼 B站連結。"
            ) from e
        raise
    return "video"


def load_wav(path):
    with wave.open(path, "rb") as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() == 2:
            data = data.reshape(-1, 2).mean(axis=1).astype(np.int16)
        return data.astype(np.float32) / 32768.0


# ===================== 核心流水线（同步函数，FastAPI 自动调度到线程池，不阻塞事件循环） =====================
def run_pipeline(job_id: str, url: str, voice: str, target_lang: str, translations=None):
    job = JOBS / job_id
    job.mkdir(exist_ok=True)
    status = {"stage": "下载视频", "progress": 5, "error": None, "done": False, "machine_translated": None}
    (job / "status.json").write_text(json.dumps(status, ensure_ascii=False))

    def set_status(stage, prog, **extra):
        status.update(stage=stage, progress=prog, **extra)
        (job / "status.json").write_text(json.dumps(status, ensure_ascii=False))

    try:
        # 1. 下载
        if not (job / "input.mp4").exists():
            title = download_video(url, job / "input.mp4")
            (job / "title.txt").write_text(title)

        # 2. 提取音轨
        if not (job / "audio.wav").exists():
            set_status("提取音轨", 20)
            sh(["ffmpeg", "-y", "-v", "error", "-i", str(job / "input.mp4"),
                "-vn", "-ac", "1", "-ar", "16000", str(job / "audio.wav")])

        # 3. 语音识别
        if not (job / "asr.json").exists():
            set_status("识别语音（AI）", 35)
            from faster_whisper import WhisperModel
            model = WhisperModel(MODEL_PATH, device="cpu", compute_type="int8")
            segments, _ = model.transcribe(str(job / "audio.wav"), vad_filter=True, beam_size=5)
            segs = [{"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
                    for s in segments]
            (job / "asr.json").write_text(json.dumps(segs, ensure_ascii=False))
        segs = json.loads((job / "asr.json").read_text())

        # 3.5 无人声检测：纯音乐/无声演示类视频没有台词可翻译，给出明确提示而非报错
        if not segs:
            raise ValueError(
                "這個視頻沒有人說話（只有背景音樂或環境聲），沒有台詞可以翻譯配音。"
                "請改用有講解的做菜視頻（例如有旁白教學的）。"
            )

        # 4. 翻译（人工注入优先；机翻失败降级为源语言清晰配音）
        if (job / "translated.json").exists() and not translations:
            translated = json.loads((job / "translated.json").read_text())
        elif translations:
            if len(translations) != len(segs):
                raise ValueError(f"译文段数 {len(translations)} 与识别段数 {len(segs)} 不一致")
            translated = [t.strip() for t in translations]
            set_status("使用人工译文", 55, machine_translated=False)
            import shutil as _sh
            _sh.rmtree(job / "tts", ignore_errors=True)  # 旧缓存必须清空，避免混入旧声音产物
        else:
            set_status("翻译为印尼语", 55)
            translated = translate_segments(segs, target_lang, status)
        (job / "translated.json").write_text(json.dumps(translated, ensure_ascii=False))

        # 4.5 文本语言与声音匹配检查：微软 TTS 拒绝跨语言混搭（会返回空音频）
        if status.get("machine_translated") is False and _cjk_ratio(translated) > 0.3:
            voice = "zh-HK-HiuMaanNeural"  # 降级为粤语女声，避免空音频
            set_status("翻译通道不可用·已切换粤语配音", 68, machine_translated=False,
                       voice_fallback=True)
        (job / "voice.txt").write_text(voice)

        # 5. 配音（线程内新建事件循环，与主服务隔离）
        set_status("生成印尼语配音", 70)
        asyncio.run(synth_voice(translated, segs, job, voice))

        # 6. 混音
        set_status("混音合成", 85)
        mix_audio(job)

        # 7. 烧录字幕
        set_status("烧录字幕", 92)
        burn_subtitles(job, segs, translated)

        set_status("完成", 100)
        status["done"] = True
        _usage_add(videos=1)  # 用量统计：完成一条视频
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "")[-400:] if isinstance(e.stderr, str) else ""
        low = detail.lower()
        if any(k in low for k in ("cookie", "sign in", "bot", "confirm you're", "http error 403", "429")):
            status.update(stage="失败", error="雲服務器 IP 被 YouTube 風控，無法直接下載。請改用「上傳視頻檔」或貼 B站連結。")
        else:
            status.update(stage="失败", error=f"視頻處理出錯，請換一個視頻再試（技術細節：{detail.strip()[:120]}）")
    except Exception as e:
        status.update(stage="失败", error=str(e)[:300])
    (job / "status.json").write_text(json.dumps(status, ensure_ascii=False))


def _cjk_ratio(texts):
    """统计 CJK 字符占比，用于判断译文是否仍是中文"""
    if not texts:
        return 0.0
    total = cjk = 0
    for t in texts:
        for ch in t:
            if not ch.isspace():
                total += 1
                if '\u4e00' <= ch <= '\u9fff':
                    cjk += 1
    return cjk / max(total, 1)


def _bad_mt(t, src):
    """识别机翻接口返回的错误页/无效译文"""
    if not t or t == src:
        return True
    if "Error 5" in t or "an error" in t.lower() or t.startswith("!!"):
        return True
    return False


def _dedupe_text(t):
    """机翻偶发把同一句重复输出 2-4 次，按句去重（否则配音超长溢出、字幕双重）"""
    parts = [p.strip() for p in re.split(r'(?<=[.!?。！？])\s+', t) if p.strip()]
    if len(parts) <= 1:
        return t
    out = []
    for p in parts:
        if not out or p.lower() != out[-1].lower():
            out.append(p)
    if len(out) < len(parts) and len(set(p.lower() for p in out)) == 1:
        return out[0]  # 整段就是同一句重复 N 次，只保留一句
    if len(out) < len(parts):
        return " ".join(out)
    return t


def _load_config():
    p = BASE / "config.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _aliyun_credentials(cfg=None):
    """优先读环境变量（由 GitHub Secrets 在部署时写入 .env），其次 config.json。"""
    cfg = cfg if cfg is not None else _load_config()
    ak = (os.environ.get("ALIYUN_ACCESS_KEY_ID") or cfg.get("aliyun_access_key_id") or "").strip()
    sk = (os.environ.get("ALIYUN_ACCESS_KEY_SECRET") or cfg.get("aliyun_access_key_secret") or "").strip()
    endpoint = (
        os.environ.get("ALIYUN_MT_ENDPOINT")
        or cfg.get("aliyun_mt_endpoint")
        or "mt.aliyuncs.com"
    ).strip()
    source_lang = (
        os.environ.get("ALIYUN_SOURCE_LANG")
        or cfg.get("aliyun_source_lang")
        or "auto"
    ).strip()
    return ak, sk, endpoint, source_lang


# ===================== 用量监测与提醒（中介后台专用） =====================
USAGE_FILE = BASE / "usage.json"

def _month_key():
    return time.strftime("%Y-%m")

def _usage_load():
    try:
        d = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    if d.get("month") != _month_key():  # 跨月自动清零
        d = {"month": _month_key(), "videos": 0, "chars": 0, "alerts": {}}
    return d

def _usage_save(d):
    USAGE_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")

def _jobs_disk_gb():
    total = 0
    if JOBS.exists():
        for p in JOBS.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    return round(total / 1024**3, 2)

def _usage_snapshot():
    d = _usage_load()
    cfg = _load_config()
    lim = cfg.get("usage_limits") or {}
    thr = float((cfg.get("alert") or {}).get("threshold_pct", 80))
    m = [
        {"key": "videos", "name": "本月视频处理条数", "used": d["videos"],
         "limit": int(lim.get("monthly_videos", 100)), "unit": "条"},
        {"key": "chars", "name": "本月翻译字符数", "used": d["chars"],
         "limit": int(lim.get("monthly_chars", 4000000)), "unit": "字符"},
        {"key": "disk", "name": "视频存储空间", "used": _jobs_disk_gb(),
         "limit": float(lim.get("disk_gb", 20)), "unit": "GB"},
    ]
    for x in m:
        x["pct"] = round(x["used"] / x["limit"] * 100, 1) if x["limit"] > 0 else 0
        x["warn"] = x["pct"] >= thr
        x["over"] = x["pct"] >= 100
    return {"month": d["month"], "metrics": m, "alerts": list(d.get("alerts", {}).values()),
            "threshold": thr}

def _send_alert_email(subject, body):
    """通过 SMTP 发送用量提醒（需在 config.json 配置 smtp 与收件邮箱；未配置则跳过）"""
    cfg = _load_config()
    a = cfg.get("alert") or {}
    to_list = a.get("emails") or []
    if not to_list or not a.get("smtp_host"):
        return False, "未配置邮件通道（在 config.json 的 alert 段填入 smtp 与 emails）"
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.header import Header
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg["From"] = a["smtp_user"]
        msg["To"] = ", ".join(to_list)
        port = int(a.get("smtp_port", 465))
        s = smtplib.SMTP_SSL(a["smtp_host"], port, timeout=15) if port == 465 \
            else smtplib.SMTP(a["smtp_host"], port, timeout=15)
        if port != 465:
            s.starttls()
        s.login(a["smtp_user"], a.get("smtp_pass", ""))
        s.sendmail(a["smtp_user"], to_list, msg.as_string())
        s.quit()
        return True, "已发送至 " + ", ".join(to_list)
    except Exception as e:
        return False, f"邮件发送失败：{e}"

def _check_usage_alerts():
    """每次任务完成后调用：用量达到阈值（默认 80%）/ 达到上限（100%）时各提醒一次"""
    snap = _usage_snapshot()
    d = _usage_load()
    changed = False
    for m in snap["metrics"]:
        for lvl in (snap.get("threshold", 80), 100):
            if m["pct"] >= lvl and f"{m['key']}_{lvl}" not in d["alerts"]:
                tag = "接近上限" if lvl < 100 else "已达上限"
                body = (f"【Onward 用量提醒】{m['name']}{tag}："
                        f"{m['used']} / {m['limit']} {m['unit']}（{m['pct']}%）。\n"
                        f"请到管理后台查看用量并考虑充值/调高配额。")
                ok, info = _send_alert_email(f"Onward 用量提醒：{m['name']} {m['pct']}%", body)
                d["alerts"][f"{m['key']}_{lvl}"] = {
                    "time": time.strftime("%Y-%m-%d %H:%M"),
                    "msg": f"⚠ {m['name']}{tag}（{m['pct']}%）：{m['used']} / {m['limit']} {m['unit']}",
                    "email": info,
                }
                changed = True
    if changed:
        _usage_save(d)

def _usage_add(videos=0, chars=0):
    try:
        d = _usage_load()
        d["videos"] += videos
        d["chars"] += chars
        _usage_save(d)
        if videos or chars:
            _check_usage_alerts()
    except Exception:
        pass  # 用量统计绝不影响主流程


def _aliyun_translate_one(text, target_lang, cfg):
    """阿里云机器翻译 TranslateGeneral（POP RPC HMAC-SHA1，纯标准库 + requests）"""
    import base64, hashlib, hmac, uuid
    from urllib.parse import quote_plus
    import requests

    ak, sk, endpoint, source_lang = _aliyun_credentials(cfg)
    if not ak or not sk:
        raise IOError("未配置阿里云 AccessKey")

    def percent_encode(s):
        # 阿里云 POP 签名要求：空格为 %20，且 ~ 不编码
        return quote_plus(str(s), safe="~").replace("+", "%20").replace("*", "%2A").replace("%7E", "~")

    params = {
        "Format": "JSON",
        "Version": "2018-10-12",
        "AccessKeyId": ak,
        "SignatureMethod": "HMAC-SHA1",
        "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "SignatureVersion": "1.0",
        "SignatureNonce": str(uuid.uuid4()),
        "Action": "TranslateGeneral",
        "FormatType": "text",
        "SourceLanguage": source_lang,
        "TargetLanguage": target_lang,
        "SourceText": text,
        "Scene": "general",
    }
    canonical = "&".join(
        f"{percent_encode(k)}={percent_encode(params[k])}" for k in sorted(params)
    )
    string_to_sign = f"POST&{percent_encode('/')}&{percent_encode(canonical)}"
    signature = base64.b64encode(
        hmac.new((sk + "&").encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    ).decode("utf-8")
    params["Signature"] = signature

    r = requests.post(f"https://{endpoint}/", data=params, timeout=15)
    d = r.json() if r.content else {}
    code = str(d.get("Code", ""))
    if code and code not in ("200", "OK"):
        raise IOError(f"{code}:{str(d.get('Message', ''))[:80]}")
    data = d.get("Data") or {}
    return (data.get("Translated") or data.get("TranslatedText") or "").strip()


def translate_segments(segs, target_lang, status=None):
    """
    翻译接口：阿里云机器翻译为主通道（密钥来自 GitHub Secrets → 部署写入 .env），
    未配置或调用失败时降级 Google，再失败保留原文。
    """
    texts = [s["text"] for s in segs]
    cfg = _load_config()
    ak, sk, _, _ = _aliyun_credentials(cfg)
    # 1) 阿里云主通道（GitHub Secrets / 环境变量 / config.json 任一有密钥即启用）
    if ak and sk:
        try:
            out = []
            for i, t in enumerate(texts):
                got = None
                last_err = None
                for _ in range(2):  # 每段一次重试
                    try:
                        got = _aliyun_translate_one(t, target_lang, cfg)
                        if not _bad_mt(got, t):
                            break
                        got = None
                    except Exception as e:
                        last_err = e
                        got = None
                if i == 0 and got is None and last_err is not None:
                    raise last_err  # 首段失败 → 整通道放弃，走 Google
                out.append(got or t)
            if status is not None:
                status["machine_translated"] = True
            return [_dedupe_text(t) for t in out]
        except Exception:
            pass
    # 2) Google 降级兜底（未配置 GitHub Secrets / 阿里云失败时）
    try:
        from deep_translator import GoogleTranslator
        tr = GoogleTranslator(source="auto", target=target_lang)
        probe = tr.translate(texts[0])  # 探测：不通就快速放弃
        if _bad_mt(probe, texts[0]):
            raise IOError("通道不可用")
        out = [probe]
        for t in texts[1:]:
            got = None
            for _ in range(2):  # 每段一次重试，缓解间歇性失败
                try:
                    got = tr.translate(t)
                    if not _bad_mt(got, t):
                        break
                    got = None
                except Exception:
                    got = None
            out.append(got or t)
        if status is not None:
            status["machine_translated"] = True
        return [_dedupe_text(t) for t in out]
    except Exception:
        if status is not None:
            status["machine_translated"] = False
        return texts


async def synth_voice(translated, segs, job, voice):
    import edge_tts
    import random
    ttsdir = job / "tts"
    ttsdir.mkdir(exist_ok=True)
    sem = asyncio.Semaphore(3)  # 低并发，避免微软服务限流

    async def one(i, text):
        async with sem:
            out = ttsdir / f"{i:04d}.mp3"
            if out.exists() and out.stat().st_size > 1024:
                return  # 断点续传：跳过已合成段落
            # 微软 TTS 拒绝用某语言音色读其他语言文本（返回空音频）：
            # 译文仍为中文的段落自动改用粤语女声，保证一定能出片
            seg_voice = voice
            if any('\u4e00' <= ch <= '\u9fff' for ch in text):
                seg_voice = "zh-HK-HiuMaanNeural"
            for a in range(6):
                try:
                    await edge_tts.Communicate(text, seg_voice, rate="+8%").save(str(out))
                    if out.stat().st_size < 1024:
                        raise IOError("empty audio")
                    return
                except Exception:
                    if a == 5:
                        raise
                    await asyncio.sleep(min(2 ** a, 10) + random.uniform(0, 2))

    await asyncio.gather(*[one(i, t) for i, t in enumerate(translated)])


def mix_audio(job):
    segs = json.loads((job / "asr.json").read_text())
    total = max((s["end"] for s in segs), default=60) + 3
    dub = np.zeros(int(total * SR), dtype=np.float32)
    for i, seg in enumerate(segs):
        mp3 = job / "tts" / f"{i:04d}.mp3"
        if not mp3.exists():
            continue
        wav = job / "tts" / f"{i:04d}.wav"
        sh(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-ar", str(SR), "-ac", "1", str(wav)])
        raw = load_wav(str(wav))
        dur = max(seg["end"] - seg["start"], 0.15)
        if len(raw) / SR > dur * 1.05:
            tempo = min(len(raw) / SR / dur, 1.5)
            st = job / "tts" / f"{i:04d}_s.wav"
            sh(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-af", f"atempo={tempo:.4f}",
                "-ar", str(SR), "-ac", "1", str(st)])
            raw = load_wav(str(st))
        s = int(seg["start"] * SR)
        # 配音严格限制在本段时段内（最长到下一段开始），避免与下一段配音重叠出现"两把声音"
        nxt = int(segs[i + 1]["start"] * SR) if i + 1 < len(segs) else len(dub)
        e = min(s + len(raw), nxt, len(dub))
        if e > s:
            dub[s:e] += raw[:e - s]

    sh(["ffmpeg", "-y", "-v", "error", "-i", str(job / "input.mp4"),
        "-ar", str(SR), "-ac", "1", str(job / "orig.wav")])
    orig = load_wav(str(job / "orig.wav"))
    n = max(len(orig), len(dub))
    mix = np.zeros(n, dtype=np.float32)
    # 原声闪避（ducking）：有配音的时段把原声压到 BG_VOL（消除原视频女声穿透），
    # 配音间隙保留原声的一半音量（保留背景音乐/环境声），120ms 平滑过渡避免突兀
    env = np.zeros(n, dtype=np.float32)
    for seg in segs:
        s = int(seg["start"] * SR)
        e = min(int(seg["end"] * SR) + int(0.3 * SR), n)
        if e > s:
            env[s:e] = 1.0
    k = int(0.12 * SR)
    c = np.cumsum(np.insert(env, 0, 0.0))
    idx = np.arange(n)
    hi = np.minimum(idx + k, n)
    env = (c[hi] - c[idx]) / np.maximum(hi - idx, 1)
    gain = (1.0 - env) * 0.5 + env * BG_VOL
    mix[:len(orig)] += orig * gain[:len(orig)]
    mix[:len(dub)] += dub * 0.95
    peak = np.abs(mix).max()
    if peak > 0.95:
        mix *= 0.95 / peak
    out = np.clip(mix * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(job / "mix.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(out.tobytes())


def burn_subtitles(job, segs, translated):
    def fmt(t):
        h, m = int(t // 3600), int(t % 3600 // 60)
        s, ms = int(t % 60), int(t % 1 * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    srt = job / "id.srt"
    with open(srt, "w") as f:
        for i, seg in enumerate(segs):
            f.write(f"{i+1}\n{fmt(seg['start'])} --> {fmt(seg['end'])}\n{translated[i]}\n\n")
    shutil.copy(srt, job / "download.srt")
    style = ("FontName=DejaVu Sans,FontSize=15,PrimaryColour=&H00FFFFFF,"
             "BackColour=&HA0000000,BorderStyle=3,Outline=1,Shadow=0,MarginV=36")
    # Windows 路径中的 "C:" 冒号会被 subtitles 滤镜当作参数分隔符，需转义为 "C\:" 并用正斜杠
    srt_esc = str(srt).replace("\\", "/").replace(":", "\\:")
    sh(["ffmpeg", "-y", "-v", "error", "-i", str(job / "input.mp4"), "-i", str(job / "mix.wav"),
        "-map", "0:v", "-map", "1:a",
        "-vf", f"subtitles='{srt_esc}':force_style='{style}'",
        "-c:v", "libx264", "-b:v", "900k", "-maxrate", "1300k", "-bufsize", "2000k",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-shortest",
        "-movflags", "+faststart", str(job / "download.mp4")], timeout=1800)


# ===================== 路由 =====================
@app.get("/health")
def health():
    """供 Nginx / 部署健康检查使用"""
    return {"status": "ok", "service": "onward-translator"}


@app.get("/", response_class=HTMLResponse)
def index():
    return (BASE / "templates" / "index.html").read_text(encoding="utf-8")


@app.post("/api/process")
def api_process(payload: dict, bg: BackgroundTasks):
    url = (payload.get("url") or "").strip()
    voice = payload.get("voice") or "id-ID-GadisNeural"
    lang = payload.get("lang") or "id"
    job_id = payload.get("job_id") or uuid.uuid4().hex[:12]
    translations = payload.get("translations")  # 可选：人工译文（与识别段一一对应）
    if not url and not translations:
        raise HTTPException(400, "请提供视频链接")
    bg.add_task(run_pipeline, job_id, url, voice, lang, translations)
    return {"job_id": job_id}


@app.post("/api/upload")
async def api_upload(bg: BackgroundTasks, file: UploadFile = File(...),
                     voice: str = Form("id-ID-GadisNeural"),
                     lang: str = Form("id")):
    """雇主手机里的视频直接上传处理（相册 / 微信收到的文件 / 录屏均可）"""
    if not (file.filename or "").lower().endswith((".mp4", ".mov", ".m4v", ".mkv", ".webm")):
        raise HTTPException(400, "请上传视频文件（MP4 / MOV 等常见格式）")
    job_id = uuid.uuid4().hex[:12]
    job = JOBS / job_id
    job.mkdir(exist_ok=True)
    size = 0
    with open(job / "input.mp4", "wb") as f:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            f.write(chunk)
    if size < 100 * 1024:
        raise HTTPException(400, "文件太小或上传失败，请重试")
    if size > 500 * 1024 * 1024:
        raise HTTPException(400, "文件过大（上限 500MB），建议先压缩或截短")
    (job / "title.txt").write_text(Path(file.filename).stem or "uploaded")
    bg.add_task(run_pipeline, job_id, "", voice, lang, None)
    return {"job_id": job_id, "size": size}


@app.get("/api/transcript/{job_id}")
def api_transcript(job_id: str):
    """导出识别稿（供人工翻译后经 /api/process 的 translations 参数重新合成）"""
    p = JOBS / job_id / "asr.json"
    if not p.exists():
        raise HTTPException(404, "识别稿不存在")
    return JSONResponse(json.loads(p.read_text()))


@app.get("/api/status/{job_id}")
def api_status(job_id: str):
    p = JOBS / job_id / "status.json"
    if not p.exists():
        raise HTTPException(404, "任务不存在")
    d = json.loads(p.read_text())
    d["has_video"] = (JOBS / job_id / "download.mp4").exists()
    d["has_srt"] = (JOBS / job_id / "download.srt").exists()
    return JSONResponse(d)


@app.get("/api/download/{job_id}/{kind}")
def api_download(job_id: str, kind: str):
    name = "download.mp4" if kind == "video" else "download.srt"
    p = JOBS / job_id / name
    if not p.exists():
        raise HTTPException(404, "文件不存在")
    title = (JOBS / job_id / "title.txt").read_text() if (JOBS / job_id / "title.txt").exists() else "video"
    safe = re.sub(r'[\\/:*?"<>|]', "", title)[:40] or "video"
    fname = f"{safe}_id-ID.mp4" if kind == "video" else f"{safe}_id-ID.srt"
    return FileResponse(p, filename=fname, media_type="video/mp4" if kind == "video" else "text/plain")


@app.get("/api/qr")
def api_qr(request: "Request"):
    """生成当前访问地址的二维码，方便手机扫码打开平台"""
    import qrcode, io
    base = str(request.base_url).rstrip("/")
    img = qrcode.make(base, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/usage")
def api_usage(key: str = ""):
    """中介管理后台：用量看板数据（管理密码保护）"""
    _check_admin(key)
    _check_usage_alerts()  # 打开后台时也评估一次，确保提醒及时生成
    return JSONResponse(_usage_snapshot())


@app.get("/api/usage/public")
def api_usage_public():
    """仅返回是否接近上限的布尔值（不带数字），供日志/监控探针用；雇主端 UI 不展示"""
    snap = _usage_snapshot()
    warns = [m["name"] for m in snap["metrics"] if m["warn"]]
    return {"warn": bool(warns), "items": warns}


def _check_admin(key: str):
    pwd = _load_config().get("admin_password")
    if not pwd:
        raise HTTPException(503, "未在 config.json 配置 admin_password，管理后台已禁用")
    if not key or key != pwd:
        raise HTTPException(401, "管理密钥不正确")


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    """中介管理后台（Onward 内部使用，勿对外分发地址）"""
    return (BASE / "templates" / "admin.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
