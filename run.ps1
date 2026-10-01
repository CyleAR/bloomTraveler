chcp 65001 > $null
$env:PYTHONUTF8 = '1'
$ErrorActionPreference = 'Stop'
$project = $PSScriptRoot
$python = Join-Path $project '.venv-modern\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw '가상환경이 없습니다. README의 설치 절차를 먼저 진행하세요.'
}
if (Get-Process -Name 'Bloom Traveler' -ErrorAction SilentlyContinue) {
    throw '기존 Bloom Traveler.exe 창을 모두 종료한 뒤 실행하세요.'
}
$script = Join-Path $project 'main.py'
$process = Start-Process -FilePath $python -ArgumentList ('"' + $script + '"') -WorkingDirectory $project -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 3
$process.Refresh()
if ($process.HasExited) { throw "앱이 종료됐습니다 (종료 코드 $($process.ExitCode))." }
Write-Output "Bloom Traveler 실행 중 (PID $($process.Id))."
