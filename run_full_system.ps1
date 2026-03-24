param(
    [string]$TargetPath = '',
    [int]$Port = 8000,
    [switch]$SkipAnalyze
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $repoRoot ".venv312\Scripts\python.exe"
$blueprint = Join-Path $repoRoot ".venv312\Scripts\blueprint.exe"

if ([string]::IsNullOrWhiteSpace($TargetPath)) {
    $TargetPath = $repoRoot
}

if (-not (Test-Path $python)) {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw "Python launcher 'py' not found. Install Python 3.12 and try again."
    }
    Write-Host "Creating local virtual environment at .venv312 ..."
    & py -3.12 -m venv (Join-Path $repoRoot ".venv312")
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker not found in PATH. Install Docker Desktop and try again."
}

Write-Host "Starting infrastructure (docker compose up -d)..."
Push-Location $repoRoot
try {
    Write-Host "Installing MAS dependencies..."
    & $python -m pip install --upgrade pip
    & $python -m pip install -e ".[dev]"

    docker compose up -d

    Write-Host "Running migrations..."
    & $python -m alembic -c "$repoRoot\migrations\pg\alembic.ini" upgrade head

    Write-Host "Starting API on port $Port..."
    $proc = Start-Process -FilePath $python -ArgumentList "-m","uvicorn","src.api.app:create_app","--factory","--host","127.0.0.1","--port",$Port -WorkingDirectory $repoRoot -PassThru

    Write-Host "Waiting for /health..."
    $healthUrl = "http://127.0.0.1:$Port/health"
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $resp = Invoke-WebRequest -UseBasicParsing $healthUrl -TimeoutSec 2
            if ($resp.StatusCode -eq 200) { $ready = $true; break }
        } catch {
            Start-Sleep -Seconds 1
        }
    }

    if (-not $ready) {
        throw "API did not become ready at $healthUrl"
    }

    if (-not $SkipAnalyze) {
        Write-Host "Running analyze against: $TargetPath"
        & $blueprint analyze --path $TargetPath
    }

    Write-Host ""
    Write-Host "API running at $healthUrl"
    Write-Host "Stop API with: Stop-Process -Id $($proc.Id)"
    if ($SkipAnalyze) {
        Write-Host "Analyze step skipped. Run manually with: $blueprint analyze --path `"$TargetPath`""
    }
} finally {
    Pop-Location
}
