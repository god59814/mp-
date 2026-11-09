#Requires -Version 5.1
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Sources
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ScriptDir
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"

function Write-Title {
    param([string]$Text)
    Write-Host ""
    Write-Host "=== $Text ===" -ForegroundColor Cyan
}

function Test-CommandExists {
    param([string]$Name)
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Ensure-Python {
    if (-not (Test-CommandExists "python")) {
        throw "找不到 Python。請先安裝 Python 3.11+ 並加入 PATH。"
    }

    $version = python --version 2>&1
    Write-Host "[OK] Python：$version"
}

function Ensure-PipPackages {
    Write-Title "檢查 Python 套件"

    $required = @(
        @{ Module = "faster_whisper"; Package = "faster-whisper" },
        @{ Module = "yt_dlp"; Package = "yt-dlp[default]" },
        @{ Module = "ctranslate2"; Package = "ctranslate2" }
    )

    $missing = @()
    foreach ($item in $required) {
        python -c "import $($item.Module)" 2>$null
        if ($LASTEXITCODE -ne 0) {
            $missing += $item.Package
            Write-Host "[缺少] $($item.Package)" -ForegroundColor Yellow
        } else {
            Write-Host "[OK] $($item.Package)"
        }
    }

    if ($missing.Count -gt 0) {
        Write-Host ""
        Write-Host "正在安裝缺少的套件..." -ForegroundColor Yellow
        python -m pip install -r (Join-Path $ScriptDir "requirements.txt")
        if ($LASTEXITCODE -ne 0) {
            throw "套件安裝失敗，請手動執行：python -m pip install -r requirements.txt"
        }
        Write-Host "[OK] 套件安裝完成" -ForegroundColor Green
    }
}

function Ensure-Ffmpeg {
    Write-Title "檢查 ffmpeg"

    if (-not (Test-CommandExists "ffmpeg")) {
        Write-Host "[缺少] ffmpeg" -ForegroundColor Yellow
        Write-Host "請先安裝 ffmpeg，例如：" -ForegroundColor Yellow
        Write-Host "  winget install Gyan.FFmpeg"
        throw "缺少 ffmpeg，無法轉換音訊。"
    }

    $version = ffmpeg -version 2>&1 | Select-Object -First 1
    Write-Host "[OK] $version"
}

function Show-OptionalWarnings {
    Write-Title "YouTube 額外需求"

    if (Test-CommandExists "deno") {
        $version = deno --version 2>&1 | Select-Object -First 1
        Write-Host "[OK] Deno：$version"
        return
    }

    if (Test-CommandExists "node") {
        $version = node --version 2>&1
        Write-Host "[OK] Node.js：$version"
        return
    }

    Write-Host "[提醒] 未偵測到 Deno / Node.js。" -ForegroundColor Yellow
    Write-Host "YouTube 連結可能下載失敗；Bilibili 通常仍可正常使用。" -ForegroundColor Yellow
    Write-Host "建議安裝 Deno：winget install DenoLand.Deno"
}

function Invoke-Transcribe {
    param([string]$Source)

    Write-Host ""
    Write-Host "開始處理：$Source" -ForegroundColor Green
    Write-Host ""

    $python = (Get-Command python).Source
    $scriptPath = Join-Path $ScriptDir "dl_mp3.py"
    $outputDir = Join-Path $ScriptDir "output"
    $liveLog = Join-Path $outputDir "_live_status.log"
    $stdoutLog = Join-Path $outputDir "_ps_stdout.log"
    $stderrLog = Join-Path $outputDir "_ps_stderr.log"

    New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
    foreach ($f in @($liveLog, $stdoutLog, $stderrLog)) {
        if (Test-Path -LiteralPath $f) {
            Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Host "正在啟動 Python / 載入套件..." -ForegroundColor DarkGray

    $proc = Start-Process -FilePath $python `
        -ArgumentList @("-u", $scriptPath, $Source) `
        -WorkingDirectory $ScriptDir `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -NoNewWindow `
        -PassThru

    $shownLive = 0
    $shownErr = 0

    try {
        while ($true) {
            if (Test-Path -LiteralPath $liveLog) {
                $lines = @(Get-Content -LiteralPath $liveLog -Encoding UTF8 -ErrorAction SilentlyContinue)
                for ($i = $shownLive; $i -lt $lines.Count; $i++) {
                    Write-Host $lines[$i]
                }
                $shownLive = $lines.Count
            }

            if (Test-Path -LiteralPath $stderrLog) {
                $errLines = @(Get-Content -LiteralPath $stderrLog -Encoding UTF8 -ErrorAction SilentlyContinue)
                for ($i = $shownErr; $i -lt $errLines.Count; $i++) {
                    if (-not [string]::IsNullOrWhiteSpace($errLines[$i])) {
                        Write-Host $errLines[$i] -ForegroundColor DarkYellow
                    }
                }
                $shownErr = $errLines.Count
            }

            if ($proc.HasExited) {
                Start-Sleep -Milliseconds 300
                $more = $false
                if (Test-Path -LiteralPath $liveLog) {
                    $lines = @(Get-Content -LiteralPath $liveLog -Encoding UTF8 -ErrorAction SilentlyContinue)
                    if ($lines.Count -gt $shownLive) {
                        for ($i = $shownLive; $i -lt $lines.Count; $i++) {
                            Write-Host $lines[$i]
                        }
                        $shownLive = $lines.Count
                        $more = $true
                    }
                }
                if (Test-Path -LiteralPath $stderrLog) {
                    $errLines = @(Get-Content -LiteralPath $stderrLog -Encoding UTF8 -ErrorAction SilentlyContinue)
                    if ($errLines.Count -gt $shownErr) {
                        for ($i = $shownErr; $i -lt $errLines.Count; $i++) {
                            if (-not [string]::IsNullOrWhiteSpace($errLines[$i])) {
                                Write-Host $errLines[$i] -ForegroundColor DarkYellow
                            }
                        }
                        $shownErr = $errLines.Count
                        $more = $true
                    }
                }
                if (-not $more) { break }
            }

            Start-Sleep -Milliseconds 200
        }
    } finally {
        if (-not $proc.HasExited) { $proc.WaitForExit() }
        else { $proc.WaitForExit() }
        $proc.Refresh()
    }

    # Start-Process 有時 ExitCode 會是 $null；在 PowerShell 裡 ($null -ne 0) 為 True，會被誤判失敗
    $exitCode = $proc.ExitCode
    if ($null -eq $exitCode) {
        $exitCode = 1
        if (Test-Path -LiteralPath $liveLog) {
            if (Select-String -LiteralPath $liveLog -SimpleMatch "全部完成" -Quiet -ErrorAction SilentlyContinue) {
                $exitCode = 0
            }
        }
    }

    if ($exitCode -ne 0) {
        Write-Host ""
        Write-Host "處理失敗，請檢查上方錯誤訊息。" -ForegroundColor Red
        return $false
    }

    Write-Host ""
    Write-Host "完成！文字檔已輸出到 output\text" -ForegroundColor Green
    return $true
}

function Start-InteractiveLoop {
    Write-Title "語音轉文字工具"
    Write-Host "支援："
    Write-Host "  - YouTube / Bilibili / yt-dlp 可解析的網站"
    Write-Host "  - 本機 mp3、wav、m4a"
    Write-Host "  - 本機 mp4（會抽出音訊再轉文字）"
    Write-Host "輸出位置：$ScriptDir\output\text"
    Write-Host ""
    Write-Host "也可把檔案拖到 start.cmd 上啟動"
    Write-Host "輸入 q 或 exit 可結束程式"
    Write-Host ""

    while ($true) {
        $source = Read-Host "請貼上影片連結或本機檔案路徑"

        if ([string]::IsNullOrWhiteSpace($source)) {
            Write-Host "未輸入內容，請再試一次。" -ForegroundColor Yellow
            continue
        }

        $source = $source.Trim().Trim('"').Trim("'")
        if ($source -in @("q", "Q", "exit", "quit")) {
            Write-Host "已結束。"
            break
        }

        [void](Invoke-Transcribe -Source $source)
        Write-Host ""
    }
}

try {
    Write-Title "環境檢查"
    Ensure-Python
    Ensure-PipPackages
    Ensure-Ffmpeg
    Show-OptionalWarnings

    if ($Sources -and $Sources.Count -gt 0) {
        Write-Title "處理拖入或指定的檔案"
        foreach ($item in $Sources) {
            $item = $item.Trim().Trim('"').Trim("'")
            if ([string]::IsNullOrWhiteSpace($item)) {
                continue
            }
            [void](Invoke-Transcribe -Source $item)
            Write-Host ""
        }
    }

    Start-InteractiveLoop
} catch {
    Write-Host ""
    Write-Host "錯誤：$($_.Exception.Message)" -ForegroundColor Red
    Read-Host "按 Enter 結束"
    exit 1
}
