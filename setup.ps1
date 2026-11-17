param([string]$PythonPath)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'

if (-not $PythonPath) {
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($version in @('-3.12', '-3.11', '-3.13')) {
            try {
                $candidate = & py $version -c 'import sys; print(sys.executable)' 2>$null
                if ($LASTEXITCODE -eq 0) { $candidates += $candidate }
            } catch { continue }
        }
    }
    $candidates += (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $candidates += (Get-Command python).Source
    }
    foreach ($candidate in $candidates) {
        if ($candidate -like '*\Microsoft\WindowsApps\*') { continue }
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        & $candidate -c 'import sys; sys.exit(not ((3,11) <= sys.version_info[:2] < (3,14)))' 2>$null
        if ($LASTEXITCODE -eq 0) { $PythonPath = $candidate; break }
    }
}
if (-not $PythonPath) {
    throw 'Python 3.12를 설치한 뒤 다시 실행하거나 -PythonPath에 python.exe 경로를 지정하세요.'
}
Write-Host "Python: $PythonPath"
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $PythonPath -m venv .venv
    if ($LASTEXITCODE -ne 0 -and -not (Test-Path -LiteralPath $venvPython)) {
        throw '가상환경 생성에 실패했습니다.'
    }
}
$hasPip = $false
try {
    & $venvPython -m pip --version 2>$null
    $hasPip = $LASTEXITCODE -eq 0
} catch { $hasPip = $false }
if (-not $hasPip) {
    & $PythonPath -m pip --python $venvPython install pip setuptools wheel
    if ($LASTEXITCODE -ne 0) { throw '가상환경 pip 설치에 실패했습니다.' }
}
$minor = & $venvPython -c 'import sys; print(sys.version_info.minor)'
if ($minor -eq '12' -and (Test-Path -LiteralPath 'requirements-windows-py312.txt')) {
    & $venvPython -m pip install -r requirements-windows-py312.txt
    if ($LASTEXITCODE -ne 0) { throw '검증된 패키지 설치에 실패했습니다.' }
}
& $venvPython -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw '프로젝트 설치에 실패했습니다.' }
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
Write-Host '준비 완료. VS Code에서 이 폴더를 열고 .venv 인터프리터를 선택하세요.'
Write-Host '키 없는 검사: .\.venv\Scripts\python.exe run.py --mode offline --save-intermediate'
Write-Host 'CrewAI 실행: .env에 본인 키를 입력한 뒤 F5에서 2번을 선택하세요.'
