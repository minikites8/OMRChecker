[CmdletBinding()]
param(
    [int]$Port = 8765,
    [switch]$SelfTest
)

$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = $null
$runtimeRoot = Join-Path $projectRoot 'tmp'

if (Test-Path -LiteralPath $runtimeRoot) {
    $python = Get-ChildItem -LiteralPath $runtimeRoot -Directory -Filter 'omr_test_env_*' |
        Sort-Object LastWriteTime -Descending |
        ForEach-Object { Join-Path $_.FullName 'Scripts\python.exe' } |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
}
if (-not $python) {
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython) { $python = $venvPython }
}
if (-not $python) { $python = (Get-Command python -ErrorAction Stop).Source }

if ($SelfTest) {
    Write-Host '正在检查 OMR 扫描台...'
    & $python (Join-Path $projectRoot 'scan_ui.py') --self-test
    exit $LASTEXITCODE
}

Write-Host '正在启动 OMR 扫描台，浏览器将自动打开。'
& $python (Join-Path $projectRoot 'scan_ui.py') --host 127.0.0.1 --port $Port --open