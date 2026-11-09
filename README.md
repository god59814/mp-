# mp-

YouTube / Bilibili / 本機影音 → 下載或抽出音訊 → Whisper GPU 語音轉文字。

## 功能

- 支援 YouTube、Bilibili，以及其他 yt-dlp 可解析的網站
- 支援本機檔案：
  - 音訊：`.mp3` `.wav` `.m4a` `.flac` `.ogg` `.aac`
  - 影片：`.mp4` `.mkv` `.webm` `.mov` `.avi`（先抽出音訊再轉寫）
- 使用 `faster-whisper` + CUDA GPU 轉寫
- Windows 可雙擊 `start.cmd` 互動使用
- 終端機顯示下載 / 轉寫進度與狀態，不洗版印出逐句文字
- 轉寫結果輸出到 `output/text/`

## 環境需求

- Windows 10/11（建議）
- Python 3.11+
- NVIDIA GPU + 可用的 CUDA 環境（本專案預設 `device=cuda`）
- [ffmpeg](https://ffmpeg.org/)
- YouTube 下載建議安裝 [Deno](https://deno.land/)（JS runtime）

## 安裝

```bash
git clone https://github.com/god59814/mp-.git
cd mp-
pip install -r requirements.txt
```

安裝 ffmpeg（Windows）：

```bash
winget install Gyan.FFmpeg
```

安裝 Deno（YouTube 建議）：

```bash
winget install DenoLand.Deno
```

安裝後請重新開啟終端機，確認：

```bash
python --version
ffmpeg -version
deno --version
```

## 使用方式

### 方法 1：雙擊啟動（推薦）

1. 雙擊 `start.cmd`
2. 程式會檢查 Python 套件、ffmpeg、Deno
3. 貼上連結或本機檔案路徑，按 Enter
4. 可連續處理；輸入 `q` 或 `exit` 結束

也可以把檔案直接拖到 `start.cmd` 上啟動。

### 方法 2：命令列

```bash
python dl_mp3.py "https://www.youtube.com/watch?v=xxxxx"
python dl_mp3.py "https://www.bilibili.com/video/BVxxxxx"
python dl_mp3.py "C:\path\to\audio.mp3"
python dl_mp3.py "C:\path\to\video.mp4"
```

不帶參數執行時，也會進入貼連結模式：

```bash
python dl_mp3.py
```

## 輸出位置

```text
output/
  audio/   # 下載或抽出的暫存音檔（預設轉寫完會刪除）
  text/    # 轉寫完成的 .txt
```

文字檔內容格式示例：

```text
[0.00 - 3.20] Hello everyone
[3.20 - 7.80] Today we are talking about...
```

## 常用設定

在 `dl_mp3.py` 頂部可調整：

| 參數 | 預設 | 說明 |
|------|------|------|
| `MODEL_SIZE` | `small` | Whisper 模型：`tiny` / `base` / `small` / `medium` / `large-v3` |
| `LANGUAGE` | `None` | `None` 自動偵測；中文可填 `"zh"` |
| `AUDIO_CODEC` | `wav` | 抽出音訊格式，也可改 `m4a` |
| `DELETE_AUDIO_AFTER_TRANSCRIBE` | `True` | 轉寫後是否刪除暫存音檔（不會刪你的原始 mp3/mp4） |
| `DEVICE` | `cuda` | 運算裝置 |
| `DEVICE_INDEX` | `0` | GPU 編號 |
| `COMPUTE_TYPE` | `float16` | VRAM 不夠可改 `int8_float16` |
| `COOKIES_FILE` | `None` | Bilibili 需登入時填 `cookies.txt` 路徑 |

## Bilibili 注意事項

- 公開影片通常可直接下載
- 需要登入的影片：匯出瀏覽器 cookies 為 `cookies.txt`，並設定 `COOKIES_FILE`
- 多分 P 影片請在網址帶 `?p=1`、`?p=2`；目前預設只下載單一指定集（`noplaylist=True`）

## YouTube 注意事項

- 新版 yt-dlp 需要 JS runtime，建議安裝 Deno
- 若遇到 403，先確認 Deno 在 PATH 中，並更新：

```bash
pip install -U "yt-dlp[default]"
```

## 專案檔案

| 檔案 | 說明 |
|------|------|
| `dl_mp3.py` | 主程式：下載 / 抽出音訊 / Whisper 轉寫 |
| `start.cmd` | Windows 雙擊啟動入口 |
| `start.ps1` | 環境檢查 + 互動輸入迴圈 |
| `requirements.txt` | Python 依賴 |

## 授權

私人專案，僅供個人使用。
