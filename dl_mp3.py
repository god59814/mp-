import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import ctranslate2
import yt_dlp
from faster_whisper import WhisperModel

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "output"
AUDIO_DIR = OUTPUT_DIR / "audio"
TRANSCRIPT_DIR = OUTPUT_DIR / "text"
LIVE_LOG = OUTPUT_DIR / "_live_status.log"
MODEL_SIZE = "small"  # 可改成 tiny / base / small / medium / large-v3
LANGUAGE = None  # 中文可填 "zh"；自動偵測請用 None
AUDIO_CODEC = "wav"  # whisper 相容性佳；也可改成 m4a
COOKIES_FILE = None  # Bilibili 需登入時可填 cookies.txt 路徑
DELETE_AUDIO_AFTER_TRANSCRIBE = True
DEVICE = "cuda"
DEVICE_INDEX = 0
COMPUTE_TYPE = "float16"  # VRAM 不夠時可改成 int8_float16
LOCAL_AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
LOCAL_VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi"}
PROGRESS_WIDTH = 28
_LAST_PROGRESS_BUCKET: dict[str, int] = {}


def configure_stdio() -> None:
    """確保在 Windows / PowerShell 下也能穩定輸出狀態。"""
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(
                encoding="utf-8",
                errors="replace",
                line_buffering=True,
                write_through=True,
            )
        except Exception:
            pass


def append_live_log(message: str) -> None:
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with LIVE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(message + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        pass


def reset_live_log() -> None:
    """確保 output 目錄存在。清空由 start.ps1 在啟動前處理。"""
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def emit(message: str = "") -> None:
    """寫入 live log（給 CMD/PS 顯示）並嘗試輸出 stdout（直接跑 python 時用）。"""
    append_live_log(message)
    try:
        print(message, flush=True)
    except Exception:
        try:
            sys.__stdout__.write(message + "\n")
            sys.__stdout__.flush()
        except Exception:
            pass


def log_status(message: str) -> None:
    emit("")
    emit(f">>> {message}")


def log_info(message: str) -> None:
    emit(f"    {message}")


def format_bytes(num: float | None) -> str:
    if not num:
        return "?"
    units = ["B", "KB", "MB", "GB"]
    value = float(num)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}B"


def format_seconds(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def render_progress(
    percent: float,
    *,
    prefix: str = "",
    suffix: str = "",
    width: int = PROGRESS_WIDTH,
    force: bool = False,
) -> None:
    """進度改成「整行輸出」，避免 PowerShell / 轉導時 \\r 進度條整段消失。"""
    percent = max(0.0, min(100.0, percent))
    bucket = 100 if percent >= 100 else int(percent // 2)
    key = prefix or "progress"
    if not force and _LAST_PROGRESS_BUCKET.get(key) == bucket and percent < 100:
        return
    _LAST_PROGRESS_BUCKET[key] = bucket

    filled = int(width * percent / 100)
    bar = "#" * filled + "-" * (width - filled)
    emit(f"{prefix}[{bar}] {percent:5.1f}% {suffix}".rstrip())


def finish_progress(message: str = "") -> None:
    if message:
        emit(message)


def reset_live_log() -> None:
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        LIVE_LOG.write_text("", encoding="utf-8")
    except Exception:
        pass


def normalize_input(text: str) -> str:
    """去掉引號、空白，並把 file:// 轉成本機路徑。"""
    text = text.strip().strip('"').strip("'")
    if text.lower().startswith("file://"):
        parsed = urlparse(text)
        path = unquote(parsed.path)
        if parsed.netloc:
            path = f"//{parsed.netloc}{path}"
        # Windows file:///C:/foo.mp3 → /C:/foo.mp3
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        return path
    return text


def looks_like_url(text: str) -> bool:
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def resolve_local_path(text: str) -> Path:
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    else:
        path = path.resolve()
    return path


def ensure_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "找不到 ffmpeg。請先安裝，例如：\n"
            "  winget install Gyan.FFmpeg"
        )
    return ffmpeg


def probe_duration_seconds(source: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return None
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def convert_local_media_to_audio(source: Path, output_dir: Path) -> Path:
    """用 ffmpeg 從本機影片/音訊抽出真正的音訊檔。"""
    ffmpeg = ensure_ffmpeg()
    output_dir.mkdir(parents=True, exist_ok=True)
    dest = output_dir / f"{source.stem}.{AUDIO_CODEC}"
    duration = probe_duration_seconds(source)

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(source),
        "-vn",
        "-progress",
        "pipe:1",
        "-nostats",
    ]
    if AUDIO_CODEC == "wav":
        cmd += ["-acodec", "pcm_s16le"]
    elif AUDIO_CODEC == "m4a":
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    elif AUDIO_CODEC == "mp3":
        cmd += ["-c:a", "libmp3lame", "-q:a", "2"]
    else:
        cmd += ["-acodec", "copy"]
    cmd.append(str(dest))

    log_status("狀態：抽出音訊中")
    log_info(f"來源：{source.name}")
    if duration:
        log_info(f"長度：{format_seconds(duration)}")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    last_out_ms = 0
    assert process.stdout is not None
    for line in process.stdout:
        line = line.strip()
        if line.startswith("out_time_ms="):
            try:
                last_out_ms = int(line.split("=", 1)[1])
            except ValueError:
                continue
            if duration and duration > 0:
                percent = min(99.9, (last_out_ms / 1_000_000) / duration * 100)
                render_progress(
                    percent,
                    prefix="抽出音訊 ",
                    suffix=f"{format_seconds(last_out_ms / 1_000_000)} / {format_seconds(duration)}",
                )
        elif line == "progress=end":
            render_progress(100, prefix="抽出音訊 ", suffix="完成")

    stderr = process.stderr.read() if process.stderr else ""
    return_code = process.wait()
    if return_code != 0 or not dest.exists():
        finish_progress()
        detail = (stderr or "").strip()
        raise RuntimeError(f"ffmpeg 轉檔失敗：{source}\n{detail[-2000:]}")

    finish_progress("抽出音訊完成")
    log_info(f"暫存音檔：{dest.name}")
    return dest


def prepare_audio(source: str) -> tuple[Path, bool]:
    """準備給 Whisper 的音訊檔。回傳 (音訊路徑, 是否為可刪除暫存檔)。"""
    if looks_like_url(source):
        return download_audio(source, AUDIO_DIR), True

    path = resolve_local_path(source)
    if not path.exists():
        raise FileNotFoundError(f"找不到檔案：{path}")
    if not path.is_file():
        raise ValueError(f"不是檔案：{path}")

    suffix = path.suffix.lower()
    if suffix in LOCAL_AUDIO_EXTS:
        log_status("狀態：準備本機音訊")
        log_info(f"偵測來源：本機音訊檔（{suffix}）")
        log_info(f"檔案：{path.name}")
        return path, False

    if suffix in LOCAL_VIDEO_EXTS:
        log_status("狀態：準備本機影片")
        log_info(f"偵測來源：本機影片檔（{suffix}）")
        return convert_local_media_to_audio(path, AUDIO_DIR), True

    supported = ", ".join(sorted(LOCAL_AUDIO_EXTS | LOCAL_VIDEO_EXTS))
    raise ValueError(f"不支援的檔案格式：{suffix}。目前支援：{supported}")


def is_bilibili_url(url: str) -> bool:
    """判斷是否為 Bilibili 網址。"""
    netloc = urlparse(url).netloc.lower()
    return "bilibili.com" in netloc or "b23.tv" in netloc


def is_youtube_url(url: str) -> bool:
    """判斷是否為 YouTube 網址。"""
    netloc = urlparse(url).netloc.lower()
    return (
        "youtube.com" in netloc
        or "youtu.be" in netloc
        or "youtube-nocookie.com" in netloc
    )


def get_http_headers(url: str) -> dict:
    """根據平台自動補 yt-dlp headers。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    if is_bilibili_url(url):
        headers.update(
            {
                "Origin": "https://www.bilibili.com",
                "Referer": "https://www.bilibili.com/",
            }
        )
    elif is_youtube_url(url):
        headers.update(
            {
                "Referer": "https://www.youtube.com/",
            }
        )

    return headers


def ensure_youtube_js_runtime() -> None:
    """YouTube 下載現在需要外部 JS runtime（建議 Deno）。"""
    if shutil.which("deno"):
        log_info("JS runtime：deno")
        return
    if shutil.which("node"):
        log_info("JS runtime：node（已啟用）")
        return

    raise RuntimeError(
        "YouTube 下載需要 JS runtime，但系統找不到 deno / node。\n"
        "請先安裝 Deno（建議）：\n"
        "  winget install DenoLand.Deno\n"
        "或安裝 Node.js，然後重新開啟終端機再執行。\n"
        "說明：https://github.com/yt-dlp/yt-dlp/wiki/EJS"
    )


def make_ytdlp_progress_hook():
    last_print = 0.0

    def hook(d: dict) -> None:
        nonlocal last_print
        status = d.get("status")
        now = time.time()

        if status == "downloading":
            if now - last_print < 0.1:
                return
            last_print = now

            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes") or 0
            speed = d.get("speed")
            eta = d.get("eta")
            if total:
                percent = downloaded / total * 100
            else:
                percent = 0.0
            suffix = (
                f"{format_bytes(downloaded)}/{format_bytes(total)} "
                f"{format_bytes(speed)}/s ETA {format_seconds(eta)}"
            )
            render_progress(percent, prefix="下載中   ", suffix=suffix)

        elif status == "finished":
            finish_progress("下載完成，準備轉碼音訊...")

    return hook


class QuietLogger:
    def debug(self, msg: str) -> None:
        return

    def info(self, msg: str) -> None:
        return

    def warning(self, msg: str) -> None:
        text = str(msg)
        if "ERROR" in text or "Unable" in text or "403" in text:
            emit(f"[警告] {text}")

    def error(self, msg: str) -> None:
        emit(f"[錯誤] {msg}")


def download_audio(url: str, output_dir: Path) -> Path:
    """用 yt-dlp 下載音訊，並透過 ffmpeg 轉成真正的音訊檔。"""
    output_dir.mkdir(parents=True, exist_ok=True)

    ydl_opts: dict = {
        "format": "bestaudio/best",
        "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": AUDIO_CODEC,
            }
        ],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": QuietLogger(),
        "progress_hooks": [make_ytdlp_progress_hook()],
        "windowsfilenames": True,
        "noplaylist": True,
        "retries": 5,
        "fragment_retries": 5,
        "http_headers": get_http_headers(url),
        # pip 安裝的 yt-dlp 需要遠端取得 EJS challenge solver
        "remote_components": ["ejs:github"],
    }
    if COOKIES_FILE:
        ydl_opts["cookiefile"] = COOKIES_FILE

    log_status("狀態：下載中")
    if is_bilibili_url(url):
        log_info("平台：Bilibili")
    elif is_youtube_url(url):
        log_info("平台：YouTube")
        ensure_youtube_js_runtime()
        # node 預設未啟用，有 deno 就用預設；只有 node 時才明確指定
        if not shutil.which("deno") and shutil.which("node"):
            ydl_opts["js_runtimes"] = {"node": {}}
        # 避開容易 403 的 android_sdkless client
        ydl_opts["extractor_args"] = {
            "youtube": {"player_client": ["default", "-android_sdkless"]}
        }
    else:
        log_info("平台：其他 yt-dlp 支援網站")
    log_info(f"網址：{url}")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        log_info("正在解析影片資訊...")
        info = ydl.extract_info(url, download=True)
        title = info.get("title") or "unknown"
        log_info(f"標題：{title}")
        audio_path = Path(ydl.prepare_filename(info)).with_suffix(f".{AUDIO_CODEC}")

    if not audio_path.exists():
        raise RuntimeError(f"下載失敗，找不到音訊檔：{audio_path}")

    log_status("狀態：下載與轉碼完成")
    log_info(f"音檔：{audio_path.name}")
    return audio_path


def build_whisper_model() -> WhisperModel:
    """建立 WhisperModel，並明確檢查 CUDA 狀態。"""
    log_status("狀態：載入模型中")
    cuda_count = ctranslate2.get_cuda_device_count()
    log_info(f"CUDA device count：{cuda_count}")

    if cuda_count <= 0:
        raise RuntimeError(
            "沒有偵測到 CUDA GPU，請檢查 NVIDIA Driver / CUDA / cuDNN / ctranslate2 環境"
        )

    if DEVICE_INDEX >= cuda_count:
        raise RuntimeError(
            f"指定的 GPU index 不存在：device_index={DEVICE_INDEX}, "
            f"目前偵測到 {cuda_count} 張 CUDA GPU"
        )

    log_info(f"模型：{MODEL_SIZE}")
    log_info(f"裝置：{DEVICE}:{DEVICE_INDEX}")
    log_info(f"compute_type：{COMPUTE_TYPE}")
    log_info(f"語言設定：{'自動偵測' if LANGUAGE is None else LANGUAGE}")

    started = time.time()
    model = WhisperModel(
        MODEL_SIZE,
        device=DEVICE,
        device_index=DEVICE_INDEX,
        compute_type=COMPUTE_TYPE,
    )
    log_info(f"模型載入完成（{time.time() - started:.1f}s）")
    return model


def transcribe_audio(audio_file: Path, transcript_dir: Path) -> Path:
    transcript_dir.mkdir(parents=True, exist_ok=True)
    model = build_whisper_model()

    log_status("狀態：轉譯中")
    log_info(f"音檔：{audio_file.name}")
    started = time.time()

    segments, info = model.transcribe(
        str(audio_file),
        language=LANGUAGE,
        vad_filter=True,
    )

    duration = getattr(info, "duration", None) or 0.0
    log_info(f"偵測語言：{info.language}")
    if duration:
        log_info(f"音訊長度：{format_seconds(duration)}")

    lines = []
    last_end = 0.0
    for segment in segments:
        text = segment.text.strip()
        if text:
            lines.append(f"[{segment.start:.2f} - {segment.end:.2f}] {text}")

        last_end = max(last_end, float(segment.end or 0.0))
        if duration > 0:
            percent = min(99.9, last_end / duration * 100)
            render_progress(
                percent,
                prefix="轉譯中   ",
                suffix=f"{format_seconds(last_end)} / {format_seconds(duration)}",
            )
        else:
            render_progress(
                0,
                prefix="轉譯中   ",
                suffix=f"已處理 {format_seconds(last_end)}",
            )

    render_progress(100, prefix="轉譯中   ", suffix="完成")
    finish_progress()

    transcript_file = transcript_dir / f"{audio_file.stem}.txt"
    transcript_file.write_text("\n".join(lines), encoding="utf-8")

    elapsed = time.time() - started
    log_status("狀態：轉譯完成")
    log_info(f"耗時：{elapsed:.1f}s")
    log_info(f"片段數：{len(lines)}")
    log_info(f"文字檔：{transcript_file}")
    return transcript_file


def run(source: str) -> Path:
    """下載或讀取本機音訊、轉文字，回傳文字檔路徑。"""
    configure_stdio()
    reset_live_log()

    source = normalize_input(source)
    if not source:
        raise ValueError("請提供網址或本機檔案路徑")

    emit("=" * 60)
    log_status("開始處理")
    log_info(f"輸入：{source}")
    log_info(f"模型：{MODEL_SIZE} | 裝置：{DEVICE}:{DEVICE_INDEX} | {COMPUTE_TYPE}")

    audio_file, is_temp = prepare_audio(source)
    transcript_file = transcribe_audio(audio_file, TRANSCRIPT_DIR)

    log_status("全部完成")
    log_info(f"輸出資料夾：{OUTPUT_DIR.resolve()}")
    log_info(f"文字檔位置：{transcript_file}")

    # 只刪下載/抽出產生的暫存音檔，絕不刪使用者的原始 mp3/mp4
    if DELETE_AUDIO_AFTER_TRANSCRIBE and is_temp and audio_file.exists():
        audio_file.unlink()
        log_info(f"已刪除暫存音檔：{audio_file.name}")

    emit("=" * 60)
    return transcript_file


def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser(description="影片/音訊下載或本機檔案 + Whisper 轉文字")
    parser.add_argument(
        "source",
        nargs="?",
        help="YouTube / Bilibili 網址，或本機 mp3 / mp4 路徑",
    )
    args = parser.parse_args()

    if args.source:
        run(args.source)
        return

    emit("請貼上影片連結或本機 mp3/mp4 路徑，按 Enter 開始：")
    source = input().strip()
    if not source:
        emit("未輸入內容，已取消。")
        sys.exit(1)
    run(source)


if __name__ == "__main__":
    main()
