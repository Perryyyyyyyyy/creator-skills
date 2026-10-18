param([string]$BootstrapPython)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
if ([IO.Path]::GetPathRoot($taskRoot) -ne 'D:\') { throw 'Install this tool on D:.' }
$taskRuntime = Join-Path $taskRoot '.runtime'
$taskTmp = Join-Path $taskRuntime 'tmp'
$taskWheels = Join-Path $taskRuntime 'wheels'
New-Item -ItemType Directory -Force -Path $taskTmp,$taskWheels | Out-Null
$env:TEMP = $taskTmp
$env:TMP = $taskTmp
$env:PIP_CACHE_DIR = Join-Path $taskRuntime 'cache/pip'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$taskPython = Join-Path $taskRuntime 'python/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    $taskArchive = Join-Path $taskRuntime 'python-3.12.10-embed-amd64.zip'
    Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' -OutFile $taskArchive -TimeoutSec 120
    if ((Get-FileHash -LiteralPath $taskArchive -Algorithm MD5).Hash -ne 'fe8ef205f2e9c3ba44d0cf9954e1abd3') { throw 'Archive integrity mismatch' }
    Expand-Archive -LiteralPath $taskArchive -DestinationPath (Split-Path $taskPython -Parent)
}
# Embedded Python searches only these explicit D: directories.
Set-Content -LiteralPath (Join-Path $taskRuntime 'python/python312._pth') -Value @('python312.zip','.','../packages-host','../packages','../../scripts') -Encoding utf8
$taskPackages = Join-Path $taskRuntime 'packages-host'
if ($BootstrapPython) {
    & $BootstrapPython -B -m pip install --only-binary=:all: --target $taskPackages --cache-dir $env:PIP_CACHE_DIR -r (Join-Path $taskRoot 'requirements.lock.txt')
} else {
    # Bootstrap pip as a wheel on D:, without installing into any system Python.
    $taskPipInfo = Invoke-RestMethod -Uri 'https://pypi.org/pypi/pip/json' -TimeoutSec 30
    $taskPipWheel = $taskPipInfo.urls | Where-Object { $_.filename.EndsWith('.whl') } | Select-Object -First 1
    if (-not $taskPipWheel) { throw 'No pip wheel returned by PyPI' }
    $taskPipPath = Join-Path $taskWheels $taskPipWheel.filename
    Invoke-WebRequest -Uri $taskPipWheel.url -OutFile $taskPipPath -TimeoutSec 120
    if ((Get-FileHash -LiteralPath $taskPipPath -Algorithm SHA256).Hash -ne $taskPipWheel.digests.sha256) { throw 'pip integrity mismatch' }
    $taskBootstrap = 'import sys; sys.path.insert(0,sys.argv.pop(1)); from pip._internal.cli.main import main; sys.exit(main(sys.argv[1:]))'
    & $taskPython -B -c $taskBootstrap $taskPipPath install --only-binary=:all: --target $taskPackages --cache-dir $env:PIP_CACHE_DIR -r (Join-Path $taskRoot 'requirements.lock.txt')
}
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& $taskPython -B (Join-Path $taskRoot 'scripts/video_breakdown.py') doctor
exit $LASTEXITCODE
