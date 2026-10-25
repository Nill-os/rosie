# Installs Rosie for this Windows user, or repairs an install. It first checks what the PC already has (Ollama and its models, Docker
# and Agent Zero) and only adds what is missing. Everything goes in %LOCALAPPDATA%\Rosie, except Ollama when it isn't installed yet
# (from winget) and the shortcuts. Run it again any time: it keeps Rosie's data.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Repo = if ($env:ROSIE_REPO) { $env:ROSIE_REPO } else { 'Nill-os/rosie' }
$Raw = "https://raw.githubusercontent.com/$Repo/main/tools/windows"
$Root = "$env:LOCALAPPDATA\Rosie"
$UvVersion = '0.12.9'
New-Item -ItemType Directory -Force "$Root\tools" | Out-Null
$env:UV_PYTHON_INSTALL_DIR = "$Root\tools\python"
$env:UV_CACHE_DIR = "$Root\tools\uv-cache"
$uv = "$Root\tools\uv.exe"
function Launcher { & $uv run --no-project --python 3.12 --managed-python "$Root\launcher.py" @args }

Write-Host '1/6  Setup tool'
if (-not (Test-Path $uv)) {
    $zip = "$Root\tools\uv.zip"
    Invoke-WebRequest "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath "$Root\tools\uv-unzip" -Force
    Copy-Item "$Root\tools\uv-unzip\uv.exe" $uv
    Remove-Item $zip, "$Root\tools\uv-unzip" -Recurse -Force
}
Invoke-WebRequest "$Raw/launcher.py" -OutFile "$Root\launcher.py"
Invoke-WebRequest "$Raw/Rosie.bat" -OutFile "$Root\Rosie.bat"
Invoke-WebRequest "$Raw/Rosie%20Google%20Sign-in.bat" -OutFile "$Root\Rosie Google Sign-in.bat"
if ($Repo -ne 'Nill-os/rosie') { Set-Content "$Root\update.json" (@{ repo = $Repo } | ConvertTo-Json) -Encoding ASCII }   # PowerShell 5.1's UTF8 adds a BOM that json can't read
& $uv python install 3.12
if ($LASTEXITCODE -ne 0) { throw 'Could not set up Python.' }

Write-Host '2/6  Checking this PC (nothing is changed)'
Launcher --check-pc

Write-Host '3/6  The AI engine (Ollama)'
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama -and (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")) { $ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
if ($ollama) { Write-Host '     Already installed.' } else {
    try {
        winget install --id Ollama.Ollama -e --silent --accept-source-agreements --accept-package-agreements
        if ($LASTEXITCODE -ne 0) { throw 'winget failed' }
    } catch {
        Write-Host '     Install Ollama from https://ollama.com/download, then run this again.'
        exit 1
    }
    Start-Process "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe" -ErrorAction SilentlyContinue
    Start-Sleep 10
}

Write-Host '4/6  Rosie (Python, libraries, and the newest tested version: 5 to 15 minutes)'
Launcher --install                                    # returns at once when the active slot is already built (a repair run rebuilds a missing one)
if ($LASTEXITCODE -ne 0) { throw 'Could not install Rosie.' }

Write-Host '5/6  Model and Agent Zero (starts Ollama and Docker Desktop if they are closed; this can take a few minutes)'
Launcher --setup

Write-Host '6/6  Shortcuts'
$sh = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    $s = $sh.CreateShortcut("$dir\Rosie.lnk"); $s.TargetPath = "$Root\Rosie.bat"; $s.WorkingDirectory = $Root; $s.WindowStyle = 7; $s.Save()
    $s = $sh.CreateShortcut("$dir\Rosie Google Sign-in.lnk"); $s.TargetPath = "$Root\Rosie Google Sign-in.bat"; $s.WorkingDirectory = $Root; $s.Save()
}
$s = $sh.CreateShortcut([Environment]::GetFolderPath('Startup') + '\Rosie.lnk'); $s.TargetPath = "$Root\Rosie.bat"; $s.WorkingDirectory = $Root
$s.Arguments = '--no-browser'; $s.WindowStyle = 7; $s.Save()
Write-Host '     Rosie starts when you sign in to Windows (to stop that: Win+R, shell:startup, delete Rosie).'

Start-Process "$Root\Rosie.bat" -WindowStyle Minimized
Write-Host ''
Write-Host 'Done. Rosie opens in your browser in a minute. Her access code is in the Rosie window.'
