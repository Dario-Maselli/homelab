# create the networks
docker network inspect shared-db-net 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Creating network shared-db-net..."
    docker network create shared-db-net
}

# run-all-compose.ps1
$folders = @(
    "core",
    "storage",
    "all_the_rrs",
    "automation",
    "databases",
    "media",
    "monitoring"
)

foreach ($folder in $folders) {
    Write-Host "Running docker compose in $folder..."
    Push-Location $folder
    if (Test-Path "docker-compose.yml") {
        docker compose down --remove-orphans
    } elseif (Test-Path "docker-compose.yaml") {
        docker compose -f docker-compose.yaml down --remove-orphans
    }
    Pop-Location
}

foreach ($folder in $folders) {
    Write-Host "Running docker compose in $folder..."
    Push-Location $folder
    if (Test-Path "docker-compose.yml") {
        docker compose up -d --force-recreate
    } elseif (Test-Path "docker-compose.yaml") {
        docker compose -f docker-compose.yaml up -d --force-recreate
    }
    Pop-Location
}

# Run with:
# .\rebuild.ps1
# OR
# powershell -ExecutionPolicy Bypass -File .\rebuild.ps1
