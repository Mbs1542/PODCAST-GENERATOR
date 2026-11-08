<#
  M.B.S Studio - installs everything the app needs inside the install folder.
  Run by the setup wizard after it copies the files; also behind the "Repair installation" shortcut.

  Nothing is added to the system PATH: Python packages live in <dir>\.venv, ffmpeg in <dir>\tools\ffmpeg,
  the transcription model in <dir>\models. Only Python itself (if missing) and Ollama (if chosen) are
  installed per-user with their official installers.

  Exit codes: 0 = everything installed, 2 = core installed but an optional part failed, 1 = core failed.
#>
param(
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [switch]$Transcriber,
    [switch]$TranscriberModel,
    [switch]$Gpu,
    [switch]$LocalLlm,
    [string]$LocalModel = "gemma3:12b",
    [switch]$Repair,
    [switch]$NoPause
)

$ErrorActionPreference = "Continue"   # native tools write progress to stderr; failures are checked by exit code
$ProgressPreference = "SilentlyContinue"   # the progress bar makes Invoke-WebRequest many times slower
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:PYTHONIOENCODING = "utf-8"

$PythonVersion = "3.12.10"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
$FfmpegUrls = @("https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
                "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip")
$OllamaUrl = "https://ollama.com/download/OllamaSetup.exe"

$InstallDir = (Resolve-Path $InstallDir).Path
$LogDir = Join-Path $InstallDir "logs"
$OptionsFile = Join-Path $InstallDir "installer\options.json"
New-Item -ItemType Directory -Force $LogDir | Out-Null
Start-Transcript -Path (Join-Path $LogDir "install.log") -Append | Out-Null
$Failed = New-Object System.Collections.Generic.List[string]

# Repair re-runs with the options of the original installation
if ($Repair -and (Test-Path $OptionsFile)) {
    $saved = Get-Content $OptionsFile -Raw | ConvertFrom-Json
    $Transcriber = [bool]$saved.Transcriber
    $TranscriberModel = [bool]$saved.TranscriberModel
    $Gpu = [bool]$saved.Gpu
    $LocalLlm = [bool]$saved.LocalLlm
    if ($saved.LocalModel) { $LocalModel = $saved.LocalModel }
}
@{ Transcriber = [bool]$Transcriber; TranscriberModel = [bool]$TranscriberModel; Gpu = [bool]$Gpu;
   LocalLlm = [bool]$LocalLlm; LocalModel = $LocalModel } | ConvertTo-Json | Set-Content -Encoding ASCII $OptionsFile

$Total = 4 + [int][bool]$Transcriber + [int][bool]$LocalLlm
$script:StepNumber = 0
function Step([string]$Text) {
    $script:StepNumber++
    Write-Host ""
    Write-Host "[$script:StepNumber/$Total] $Text" -ForegroundColor Cyan
}
function Ok([string]$Text) { Write-Host "  OK  $Text" -ForegroundColor Green }
function Warn([string]$Text) { Write-Host "  !!  $Text" -ForegroundColor Yellow }

# Large downloads drop on flaky connections: try each mirror up to 3 times, preferring BITS (resumable)
function Download([string[]]$Urls, [string]$Target) {
    foreach ($url in $Urls) {
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            Write-Host "  downloading $url (attempt $attempt)"
            try {
                if (Test-Path $Target) { Remove-Item $Target -Force }
                try {
                    Start-BitsTransfer -Source $url -Destination $Target -ErrorAction Stop
                } catch {
                    Invoke-WebRequest -Uri $url -OutFile $Target -UseBasicParsing
                }
                if ((Test-Path $Target) -and (Get-Item $Target).Length -gt 0) { return }
            } catch {
                Warn "download failed: $($_.Exception.Message)"
                Start-Sleep -Seconds (5 * $attempt)
            }
        }
    }
    throw "Could not download $($Urls[0])"
}

# Every pip install uses the pinned versions: no resolver backtracking, same result on every machine
$Constraints = Join-Path $InstallDir "installer\constraints.txt"
function Pip-Install([string]$What, [string[]]$Packages) {
    $arguments = @("-m", "pip", "install") + $Packages
    if (Test-Path $Constraints) { $arguments += @("-c", $Constraints) }
    Invoke-Checked $What $venvPython $arguments
}

# Run a native command, echo its output into the log, and throw if it fails
function Invoke-Checked([string]$What, [string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments 2>&1 | ForEach-Object { "$_" } | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

function Find-Python {
    try {
        $found = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) { return "$found".Trim() }
    } catch {}
    foreach ($candidate in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                             "$env:ProgramFiles\Python312\python.exe")) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Find-Ollama {
    $command = Get-Command ollama -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $default = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (Test-Path $default) { return $default }
    return $null
}

function Test-OllamaServer {
    try { Invoke-RestMethod "http://localhost:11434/api/tags" -TimeoutSec 3 | Out-Null; return $true } catch { return $false }
}

Write-Host "M.B.S Studio setup - installing into $InstallDir"
Write-Host "Full log: $LogDir\install.log"
Push-Location $InstallDir
$exitCode = 0
try {
    # ------------------------------------------------------------------ 1. Python
    Step "Python $PythonVersion"
    $python = Find-Python
    if ($python) {
        Ok "found $python"
    } else {
        $setup = Join-Path $env:TEMP "python-$PythonVersion-amd64.exe"
        Download $PythonUrl $setup
        Write-Host "  installing Python for the current user (no admin rights needed)..."
        $process = Start-Process $setup -ArgumentList "/quiet InstallAllUsers=0 PrependPath=0 Include_launcher=1 Include_test=0" -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw "Python installer failed (exit code $($process.ExitCode))" }
        $python = Find-Python
        if (-not $python) { throw "Python was installed but could not be found" }
        Ok "installed $python"
    }

    # ------------------------------------------------------------------ 2. Packages
    Step "Python packages (a few minutes on the first install)"
    $venvPython = Join-Path $InstallDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        Invoke-Checked "Creating the virtual environment" $python @("-m", "venv", (Join-Path $InstallDir ".venv"))
    }
    Invoke-Checked "Updating pip" $venvPython @("-m", "pip", "install", "--upgrade", "pip")
    Pip-Install "Installing packages" @("-r", "requirements.txt")
    Ok "core packages installed"
    try {
        Pip-Install "Installing Manim" @("manim")
        Ok "Manim (animations) installed (pip's 'decorator' conflict warning above is expected and harmless)"
    } catch {
        Warn "Manim could not be installed; animated visuals will be unavailable. ($_)"
        $Failed.Add("Manim (animations)")
    }

    # ------------------------------------------------------------------ 3. ffmpeg
    Step "ffmpeg (audio and video processing)"
    $ffmpegBin = Join-Path $InstallDir "tools\ffmpeg\bin"
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        Ok "already on PATH"
    } elseif (Test-Path (Join-Path $ffmpegBin "ffmpeg.exe")) {
        Ok "already in tools\ffmpeg"
    } else {
        $zip = Join-Path $env:TEMP "ffmpeg-release-essentials.zip"
        $unpacked = Join-Path $env:TEMP "mbs-ffmpeg"
        Download $FfmpegUrls $zip
        if (Test-Path $unpacked) { Remove-Item $unpacked -Recurse -Force }
        Expand-Archive $zip -DestinationPath $unpacked -Force
        $exe = Get-ChildItem $unpacked -Recurse -Filter ffmpeg.exe | Select-Object -First 1
        if (-not $exe) { throw "ffmpeg.exe not found in the downloaded archive" }
        New-Item -ItemType Directory -Force $ffmpegBin | Out-Null
        Copy-Item (Join-Path $exe.DirectoryName "*") $ffmpegBin -Force
        Remove-Item $unpacked -Recurse -Force
        Remove-Item $zip -Force
        Ok "installed into tools\ffmpeg"
    }

    # ------------------------------------------------------------------ 4. Transcriber
    if ($Transcriber) {
        Step "Hebrew video transcriber (ivrit.ai Whisper)"
        try {
            Pip-Install "Installing the transcriber" @("-r", "requirements-transcribe.txt")
            Ok "transcriber installed"
            if ($Gpu) {
                try {
                    Write-Host "  NVIDIA GPU: installing CUDA libraries (about 1 GB) for fast transcription..."
                    Pip-Install "Installing CUDA libraries" @("-r", "requirements-gpu.txt")
                    Ok "GPU libraries installed"
                } catch {
                    Warn "GPU libraries failed; transcription will run on the CPU (slower). ($_)"
                    $Failed.Add("GPU libraries for the transcriber")
                }
            }
            if ($TranscriberModel) {
                Write-Host "  downloading the Hebrew model (about 1.6 GB, once)..."
                Invoke-Checked "Downloading the transcription model" $venvPython @("-m", "scripts.transcribe", "--download-model")
                Ok "model ready"
            } else {
                Ok "the model will download the first time you transcribe"
            }
        } catch {
            Warn "Transcriber setup failed: $_"
            $Failed.Add("Hebrew video transcriber")
        }
    }

    # ------------------------------------------------------------------ 5. Local model
    if ($LocalLlm) {
        Step "Local checker model (Ollama + $LocalModel)"
        try {
            $ollama = Find-Ollama
            if (-not $ollama) {
                $setup = Join-Path $env:TEMP "OllamaSetup.exe"
                Download $OllamaUrl $setup
                Write-Host "  installing Ollama..."
                $process = Start-Process $setup -ArgumentList "/VERYSILENT /NORESTART /SUPPRESSMSGBOXES" -Wait -PassThru
                if ($process.ExitCode -ne 0) { throw "Ollama installer failed (exit code $($process.ExitCode))" }
                $ollama = Find-Ollama
                if (-not $ollama) { throw "Ollama was installed but could not be found" }
            }
            Ok "Ollama: $ollama"
            if (-not (Test-OllamaServer)) {
                Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden
                for ($i = 0; $i -lt 30 -and -not (Test-OllamaServer); $i++) { Start-Sleep -Seconds 1 }
            }
            if (-not (Test-OllamaServer)) { throw "the Ollama server did not start" }
            Write-Host "  downloading $LocalModel (about 8 GB, once)..."
            & $ollama pull $LocalModel
            if ($LASTEXITCODE -ne 0) { throw "ollama pull failed (exit code $LASTEXITCODE)" }
            Ok "$LocalModel ready"
        } catch {
            Warn "Local model setup failed: $_  (the app works without it; checks then use Azure)"
            $Failed.Add("Local checker model")
        }
    }

    # ------------------------------------------------------------------ 6. Check
    Step "Checking the installation"
    & $venvPython -m scripts.doctor 2>&1 | ForEach-Object { "$_" } | Out-Host

    if ($Failed.Count -gt 0) {
        $exitCode = 2
        Write-Host ""
        Warn ("Installed, except: " + ($Failed -join ", "))
        Warn "Run 'Repair installation' from the Start menu to try these parts again."
    } else {
        Write-Host ""
        Write-Host "Installation complete." -ForegroundColor Green
    }
} catch {
    $exitCode = 1
    Write-Host ""
    Write-Host "Installation failed: $_" -ForegroundColor Red
    Write-Host "Check your internet connection and run 'Repair installation' from the Start menu."
    Write-Host "Log: $LogDir\install.log"
} finally {
    Pop-Location
    Stop-Transcript | Out-Null
}

if (-not $NoPause) {
    Write-Host ""
    Write-Host "Press any key to return to the setup wizard..."
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
}
exit $exitCode
