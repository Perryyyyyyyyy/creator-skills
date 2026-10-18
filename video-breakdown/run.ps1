# All new runtime data stays on D:. No global PATH or user settings are changed.
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
if ([IO.Path]::GetPathRoot($taskRoot) -ne 'D:\') { throw 'This tool must run from D: under the current storage preference.' }
$taskTemp = Join-Path $taskRoot '.runtime/tmp'
New-Item -ItemType Directory -Force -Path $taskTemp | Out-Null
$env:TEMP = $taskTemp
$env:TMP = $taskTemp
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
& (Join-Path $taskRoot '.runtime/python/python.exe') -B (Join-Path $taskRoot 'scripts/video_breakdown.py') @args
exit $LASTEXITCODE
