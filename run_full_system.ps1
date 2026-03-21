param(
    [string]$TargetPath = '',
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $repoRoot ".venv312\Scripts\python.exe"
$blueprint = Join-Path $repoRoot ".venv312\Scripts\blueprint.exe"

if ([string]::IsNullOrWhiteSpace($TargetPath)) {
    $TargetPath = $repoRoot
}

if (-not (Test-Path $python)) {
    throw "Missing venv at $python. Create it with: py -3.12 -m venv .venv312"
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker not found in PATH. Install Docker Desktop and try again."
}

Write-Host "Starting infrastructure (docker compose up -d)..."
Push-Location $repoRoot
try {
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

    Write-Host "Running analyze against: $TargetPath"
    & $blueprint analyze --path $TargetPath

    Write-Host ""
    Write-Host "API running at $healthUrl"
    Write-Host "Stop API with: Stop-Process -Id $($proc.Id)"
} finally {
    Pop-Location
}
