# build_release.ps1
# Builds a self-contained, no-Python-install zip for end users.
#
#   powershell -ExecutionPolicy Bypass -File build_release.ps1
#
# Output: dist\qwen-i2v-studio\  (the unpacked package)
#         dist\qwen-i2v-studio.zip  (ship this)
#
# The package bundles an embeddable Python so the user never installs Python.
# Chromium is NOT bundled (too big / per-machine); the launcher downloads it
# on first run.

$ErrorActionPreference = 'Stop'
$root    = $PSScriptRoot
$pyVer   = '3.11.9'   # stable, has wheels for all deps
$dist    = Join-Path $root 'dist'
$pkgName = 'qwen-i2v-studio'
$pkg     = Join-Path $dist $pkgName
$pyDir   = Join-Path $pkg 'python'

Write-Host "=== Qwen i2v Studio release builder ===" -ForegroundColor Cyan

# --- clean (keep a cached python\ to speed re-builds; -Fresh forces redownload) ---
$cachePy = Join-Path $dist '_python_cache'
if (Test-Path $pkg) {
    # preserve the already-downloaded embeddable python across rebuilds
    if ((Test-Path (Join-Path $pyDir 'python.exe')) -and -not $args.Contains('-Fresh')) {
        if (Test-Path $cachePy) { Remove-Item $cachePy -Recurse -Force }
        Move-Item $pyDir $cachePy
    }
    Remove-Item $pkg -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $pkg | Out-Null

# --- 1. download embeddable Python (or reuse cache) ---
if ((Test-Path (Join-Path $cachePy 'python.exe')) -and -not $args.Contains('-Fresh')) {
    Write-Host "[1/5] reusing cached embeddable Python"
    Move-Item $cachePy $pyDir
} else {
    New-Item -ItemType Directory -Force -Path $pyDir | Out-Null
    $arch = if ([Environment]::Is64BitOperatingSystem) { 'amd64' } else { 'win32' }
    $pyZipUrl = "https://www.python.org/ftp/python/$pyVer/python-$pyVer-embed-$arch.zip"
    $pyZip    = Join-Path $dist "python-embed.zip"
    Write-Host "[1/5] downloading embeddable Python $pyVer ($arch)..."
    Invoke-WebRequest -Uri $pyZipUrl -OutFile $pyZip
    Expand-Archive -Path $pyZip -DestinationPath $pyDir -Force
    Remove-Item $pyZip
}

# --- 2. enable site-packages in the embeddable build ---
# The embeddable distro ships a python3XX._pth that disables `import site`,
# which blocks pip-installed packages. Uncomment `import site` to fix that.
$pth = Get-ChildItem -Path $pyDir -Filter 'python*._pth' | Select-Object -First 1
if ($pth) {
    $lines = Get-Content $pth.FullName
    $lines = $lines -replace '^#\s*import site', 'import site'
    if ($lines -notcontains 'import site') { $lines += 'import site' }
    # also expose the package root so `import main` works from app.py's cwd
    if ($lines -notcontains '.') { $lines = @('.') + $lines }
    Set-Content -Path $pth.FullName -Value $lines -Encoding ascii
    Write-Host "[2/5] patched $($pth.Name) (site enabled)"
} else {
    Write-Warning "[2/5] no python*._pth found - embeddable layout changed?"
}

# --- 3. fetch get-pip.py + bootstrap pip + pre-install deps ---
Write-Host "[3/5] fetching get-pip.py + pre-installing libraries..."
$getpip = Join-Path $pkg 'get-pip.py'
Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $getpip
$pyExe = Join-Path $pyDir 'python.exe'
# Bootstrap pip into the embeddable interpreter (no ensurepip there).
& $pyExe $getpip --no-warn-script-location | Out-Null
# Pre-install the app's Python deps so the user's first run only needs Chromium.
# (Chromium is per-machine under %LOCALAPPDATA%, so it can't be bundled here.)
& $pyExe -m pip install --no-warn-script-location -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw "pre-install of requirements failed" }
# Drop the first-run marker so the launcher skips the slow dep step; it will
# still run `playwright install chromium` once (that part can't be bundled).
'deps-prebuilt' | Set-Content -Path (Join-Path $pyDir '.deps-prebuilt') -Encoding ascii

# --- 4. copy app files ---
# NOTE: keep this script free of non-ASCII string literals. Windows PowerShell
# 5.1 reads .ps1 as the ANSI codepage, which mangles Korean literals. Korean
# filenames are reconstructed from char codes and matched via globbing instead.
Write-Host "[4/5] copying app files..."
$files = @('app.py','main.py','download_current.py','requirements.txt')
foreach ($f in $files) {
    Copy-Item (Join-Path $root $f) -Destination $pkg -Force
}
# launcher: 실행하기.bat  (name from code points so the literal stays ASCII-safe)
$launcherName = -join ([char]0xC2E4,[char]0xD589,[char]0xD558,[char]0xAE30) + '.bat'  # "실행하기.bat"
$launcher = Join-Path $root $launcherName
if (Test-Path $launcher) {
    Copy-Item $launcher -Destination $pkg -Force
} else {
    throw "launcher not found: $launcherName (run from repo root)"
}

# end-user readme: README_USER.txt -> 사용법.txt (name built from code points)
$userReadme = Join-Path $root 'README_USER.txt'
if (Test-Path $userReadme) {
    $useGuide = -join ([char]0xC0AC,[char]0xC6A9,[char]0xBC95) + '.txt'  # "사용법.txt"
    Copy-Item $userReadme -Destination (Join-Path $pkg $useGuide) -Force
}

# --- 5. zip it ---
Write-Host "[5/5] zipping..."
$zip = Join-Path $dist "$pkgName.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $pkg -DestinationPath $zip
$sizeMB = [math]::Round((Get-Item $zip).Length / 1MB, 1)

Write-Host ""
Write-Host "DONE." -ForegroundColor Green
Write-Host "  package: $pkg"
Write-Host "  zip    : $zip  ($sizeMB MB)"
Write-Host ""
Write-Host "Test: unzip somewhere fresh, double-click 실행하기.bat"
Write-Host "Ship: gh release create vX.Y.Z `"$zip`" --title ... --notes ..."
